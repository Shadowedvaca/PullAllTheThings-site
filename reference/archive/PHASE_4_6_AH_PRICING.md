# Phase 4.6 — Auction House Pricing

## Goal

Track auction house prices for guild-selected items using Blizzard's existing AH API
(no new credentials needed). Display current prices and trends on the guild dashboard.
Hourly refresh for tracked items.

---

## Prerequisites

- Phase 4.3 complete (Blizzard client extended, last-login optimization in place)
- Blizzard API credentials working (same credentials used for roster sync)
- Guild's connected realm ID resolved

---

## Database Migration: 0035_ah_pricing

### New Table: `guild_identity.tracked_items`

```sql
CREATE TABLE guild_identity.tracked_items (
    id                  SERIAL PRIMARY KEY,
    item_id             INTEGER NOT NULL UNIQUE,    -- Blizzard item ID
    item_name           VARCHAR(200) NOT NULL,
    category            VARCHAR(50) DEFAULT 'consumable',  -- consumable, enchant, gem, material, gear
    display_order       INTEGER DEFAULT 0,          -- Sort order on dashboard
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    added_by_player_id  INTEGER REFERENCES guild_identity.players(id),
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### New Table: `guild_identity.item_price_history`

```sql
CREATE TABLE guild_identity.item_price_history (
    id                  SERIAL PRIMARY KEY,
    tracked_item_id     INTEGER NOT NULL REFERENCES guild_identity.tracked_items(id) ON DELETE CASCADE,
    snapshot_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    min_buyout          BIGINT NOT NULL,            -- Copper (divide by 10000 for gold)
    median_price        BIGINT,                     -- Copper
    mean_price          BIGINT,                     -- Copper
    quantity_available  INTEGER NOT NULL DEFAULT 0,
    num_auctions        INTEGER NOT NULL DEFAULT 0,
    connected_realm_id  INTEGER NOT NULL,
    UNIQUE (tracked_item_id, snapshot_at)
);
CREATE INDEX idx_price_history_item ON guild_identity.item_price_history(tracked_item_id);
CREATE INDEX idx_price_history_time ON guild_identity.item_price_history(snapshot_at DESC);
```

### New Column on `common.site_config`

```sql
ALTER TABLE common.site_config
    ADD COLUMN connected_realm_id INTEGER;
```

### Seed Common Consumables

Pre-populate `tracked_items` with commonly tracked raid consumables:

```sql
-- NOTE: Item IDs are for The War Within Season 2. Update when new season launches.
-- These are examples — actual IDs must be verified at implementation time.
INSERT INTO guild_identity.tracked_items (item_id, item_name, category, display_order) VALUES
    (212241, 'Flask of Alchemical Chaos', 'consumable', 1),
    (212248, 'Flask of Tempered Versatility', 'consumable', 2),
    (212246, 'Flask of Tempered Mastery', 'consumable', 3),
    (222732, 'Heartseeking Health Injector', 'consumable', 4),
    (222509, 'Enchant Weapon - Authority of Radiant Power', 'enchant', 10),
    (222510, 'Enchant Weapon - Authority of Storms', 'enchant', 11),
    (222524, 'Enchant Cloak - Chant of Leeching Fangs', 'enchant', 12),
    (213746, 'Magnificent Jeweler''s Setting', 'gem', 20)
ON CONFLICT (item_id) DO NOTHING;
```

---

## Task 1: Connected Realm Resolution

### File: `src/sv_common/guild_sync/blizzard_client.py`

New method to find the guild's connected realm:

```python
async def get_connected_realm_id(self, realm_slug: str) -> int | None:
    """
    Resolve a realm slug to its connected realm ID.

    GET /data/wow/realm/{realmSlug}
    Response includes: connected_realm.href → extract ID from URL.
    """
    path = f"/data/wow/realm/{realm_slug}"
    data = await self._get(path, namespace="dynamic-us")
    if data and "connected_realm" in data:
        # href looks like: ".../connected-realm/11?namespace=..."
        href = data["connected_realm"]["href"]
        # Extract ID from path
        match = re.search(r"/connected-realm/(\d+)", href)
        if match:
            return int(match.group(1))
    return None
```

### Resolution and Caching

On first AH sync (or during setup wizard), resolve the connected realm ID and store
in `common.site_config.connected_realm_id`. Subsequent syncs use the cached value.

---

## Task 2: Auction House Data Fetch

### File: `src/sv_common/guild_sync/ah_sync.py` (new file)

```python
"""
Auction House price sync.

Fetches the full AH snapshot from Blizzard, filters to tracked items,
computes price statistics, and stores in item_price_history.
"""

import statistics
from datetime import datetime, timezone

import asyncpg


async def sync_ah_prices(pool, blizzard_client, connected_realm_id: int) -> dict:
    """
    Fetch AH snapshot and store prices for tracked items.

    Blizzard endpoint: GET /data/wow/connected-realm/{id}/auctions
    Returns ALL auctions on the connected realm. We filter to tracked items only.
    """
    # 1. Load tracked item IDs
    async with pool.acquire() as conn:
        tracked = await conn.fetch(
            "SELECT id, item_id FROM guild_identity.tracked_items WHERE is_active = TRUE"
        )
    tracked_map = {row["item_id"]: row["id"] for row in tracked}
    if not tracked_map:
        return {"status": "no_tracked_items"}

    # 2. Fetch AH snapshot from Blizzard
    auctions = await blizzard_client.get_auctions(connected_realm_id)
    if auctions is None:
        return {"status": "api_error"}

    # 3. Filter and aggregate
    item_prices: dict[int, list[int]] = {}  # item_id → list of unit prices
    item_quantities: dict[int, int] = {}
    item_auction_counts: dict[int, int] = {}

    for auction in auctions.get("auctions", []):
        item_id = auction.get("item", {}).get("id")
        if item_id not in tracked_map:
            continue

        # unit_price for commodities, buyout for non-commodities
        price = auction.get("unit_price") or auction.get("buyout", 0)
        qty = auction.get("quantity", 1)

        if price <= 0:
            continue

        item_prices.setdefault(item_id, []).append(price)
        item_quantities[item_id] = item_quantities.get(item_id, 0) + qty
        item_auction_counts[item_id] = item_auction_counts.get(item_id, 0) + 1

    # 4. Compute stats and store
    now = datetime.now(timezone.utc)
    stats = {"items_updated": 0, "items_not_listed": 0}

    async with pool.acquire() as conn:
        for item_id, prices in item_prices.items():
            tracked_item_id = tracked_map[item_id]
            await conn.execute("""
                INSERT INTO guild_identity.item_price_history
                    (tracked_item_id, snapshot_at, min_buyout, median_price,
                     mean_price, quantity_available, num_auctions, connected_realm_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (tracked_item_id, snapshot_at) DO UPDATE SET
                    min_buyout = EXCLUDED.min_buyout,
                    median_price = EXCLUDED.median_price,
                    mean_price = EXCLUDED.mean_price,
                    quantity_available = EXCLUDED.quantity_available,
                    num_auctions = EXCLUDED.num_auctions
            """,
                tracked_item_id,
                now,
                min(prices),
                int(statistics.median(prices)),
                int(statistics.mean(prices)),
                item_quantities.get(item_id, 0),
                item_auction_counts.get(item_id, 0),
                connected_realm_id,
            )
            stats["items_updated"] += 1

        # Note items with no listings
        for item_id in tracked_map:
            if item_id not in item_prices:
                stats["items_not_listed"] += 1

    return stats


async def cleanup_old_prices(pool, days_hourly: int = 30, days_daily: int = 180):
    """
    Retain hourly data for 30 days, then aggregate to daily.
    Delete anything older than 180 days.
    """
    async with pool.acquire() as conn:
        # Delete hourly data older than 30 days (keep one per day)
        await conn.execute("""
            DELETE FROM guild_identity.item_price_history
            WHERE snapshot_at < NOW() - INTERVAL '%s days'
              AND id NOT IN (
                  SELECT DISTINCT ON (tracked_item_id, snapshot_at::date) id
                  FROM guild_identity.item_price_history
                  WHERE snapshot_at < NOW() - INTERVAL '%s days'
                  ORDER BY tracked_item_id, snapshot_at::date, snapshot_at ASC
              )
        """, days_hourly, days_hourly)

        # Delete everything older than 180 days
        await conn.execute("""
            DELETE FROM guild_identity.item_price_history
            WHERE snapshot_at < NOW() - INTERVAL '%s days'
        """, days_daily)
```

### File: `src/sv_common/guild_sync/blizzard_client.py`

New method:

```python
async def get_auctions(self, connected_realm_id: int) -> dict | None:
    """
    GET /data/wow/connected-realm/{connectedRealmId}/auctions

    Returns ALL auctions on the connected realm.
    WARNING: Large response (can be 10+ MB for busy realms).
    """
    path = f"/data/wow/connected-realm/{connected_realm_id}/auctions"
    return await self._get(path, namespace="dynamic-us")
```

**Commodities Note:** Since patch 9.2.7, commodities are region-wide. The connected realm
endpoint returns non-commodity auctions. For commodities, use:

```python
async def get_commodities(self) -> dict | None:
    """GET /data/wow/auctions/commodities — region-wide commodity auctions."""
    return await self._get("/data/wow/auctions/commodities", namespace="dynamic-us")
```

Most tracked items (flasks, enchants, gems) are commodities. The sync function should
check both endpoints or just use the commodities endpoint for commodity items.

---

## Task 3: Scheduler Integration

### File: `src/sv_common/guild_sync/scheduler.py`

New independent pipeline:

```python
async def run_ah_sync(self):
    """Auction House price sync. Runs hourly."""
    # 1. Load connected_realm_id from site_config
    # 2. If not set, resolve it and cache
    # 3. Call sync_ah_prices()
    # 4. Run cleanup_old_prices() (daily, not every hour)
    # 5. Log results
```

Add to scheduler:

```python
scheduler.add_job(self.run_ah_sync, "cron", minute=15)  # Every hour at :15
```

Runs at :15 past the hour. Blizzard AH snapshots update roughly on the hour,
so :15 gives time for the snapshot to be available.

---

## Task 4: Price Display Helpers

### File: `src/sv_common/guild_sync/ah_service.py` (new file)

```python
def copper_to_gold_str(copper: int) -> str:
    """Convert copper value to WoW gold display format."""
    gold = copper // 10000
    silver = (copper % 10000) // 100
    if gold >= 1000:
        return f"{gold:,}g"
    elif gold > 0:
        return f"{gold}g {silver}s"
    else:
        return f"{silver}s"


async def get_current_prices(pool) -> list[dict]:
    """Get latest price snapshot for all tracked items."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (ti.id)
                ti.id, ti.item_id, ti.item_name, ti.category,
                iph.min_buyout, iph.median_price, iph.quantity_available,
                iph.snapshot_at
            FROM guild_identity.tracked_items ti
            LEFT JOIN guild_identity.item_price_history iph
                ON iph.tracked_item_id = ti.id
            WHERE ti.is_active = TRUE
            ORDER BY ti.id, iph.snapshot_at DESC
        """)
    return [dict(r) for r in rows]


async def get_price_trend(pool, tracked_item_id: int, days: int = 7) -> list[dict]:
    """Get price history for charting."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT snapshot_at, min_buyout, median_price, quantity_available
            FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1
              AND snapshot_at >= NOW() - INTERVAL '%s days'
            ORDER BY snapshot_at ASC
        """, tracked_item_id, days)
    return [dict(r) for r in rows]


async def get_price_change(pool, tracked_item_id: int) -> dict:
    """Get price change vs 24 hours ago."""
    async with pool.acquire() as conn:
        current = await conn.fetchval("""
            SELECT min_buyout FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1 ORDER BY snapshot_at DESC LIMIT 1
        """, tracked_item_id)
        yesterday = await conn.fetchval("""
            SELECT min_buyout FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1
              AND snapshot_at <= NOW() - INTERVAL '24 hours'
            ORDER BY snapshot_at DESC LIMIT 1
        """, tracked_item_id)
    if current and yesterday and yesterday > 0:
        change_pct = ((current - yesterday) / yesterday) * 100
        return {"current": current, "yesterday": yesterday, "change_pct": round(change_pct, 1)}
    return {"current": current, "yesterday": None, "change_pct": None}
```

---

## Task 5: Public Display

### Option A: Section on Index Page

Add a "Market Watch" card to the index page (gated behind having tracked items configured):

```html
{% if ah_prices %}
<div class="market-watch-card">
    <h3>Market Watch</h3>
    <table>
        <tr><th>Item</th><th>Price</th><th>24h</th><th>Available</th></tr>
        {% for item in ah_prices %}
        <tr>
            <td>{{ item.item_name }}</td>
            <td>{{ item.min_buyout | gold }}</td>
            <td class="{{ 'price-up' if item.change_pct > 0 else 'price-down' }}">
                {{ item.change_pct }}%
            </td>
            <td>{{ item.quantity_available }}</td>
        </tr>
        {% endfor %}
    </table>
    <small>Updated {{ ah_prices[0].snapshot_at | timeago }}</small>
</div>
{% endif %}
```

### Option B: Dedicated Page

`GET /market` — Public page with:
- Current prices table
- 7-day price chart per item (using Chart.js or similar)
- Category filters (consumables, enchants, gems)

**Recommendation:** Start with Option A (index card), add Option B later if there's demand.

### Jinja2 Filter

Add a `gold` filter to convert copper to display format:

```python
templates.env.filters["gold"] = copper_to_gold_str
```

---

## Task 6: Admin Configuration

### New Route: `GET /admin/ah-pricing`

| Section | Content |
|---------|---------|
| **Tracked Items** | Table of items: name, category, current price, 24h change, active toggle, remove button |
| **Add Item** | Search by name (Blizzard item search API) or enter item ID manually |
| **Connected Realm** | Display current connected realm ID + realm name. Re-resolve button. |
| **Sync Status** | Last sync time, items updated, items not listed |
| **Force Sync** | Button to trigger immediate AH sync |

### Add Item Endpoint: `POST /admin/ah-pricing/items`

```json
{"item_id": 212241, "item_name": "Flask of Alchemical Chaos", "category": "consumable"}
```

### Remove Item Endpoint: `DELETE /admin/ah-pricing/items/{id}`

Soft delete (set `is_active = FALSE`) or hard delete with cascade to price history.

### Item Search (Optional Enhancement)

Blizzard has an item search API:
```
GET /data/wow/search/item?name.en_US=flask&orderby=id&_page=1
```

This could power an autocomplete search for adding items. If too complex for v1, just
use manual item ID entry with a link to Wowhead for looking up IDs.

---

## API Considerations

### Blizzard AH API Details

- **Endpoint:** `GET /data/wow/connected-realm/{id}/auctions`
- **Response size:** Can be large (10-50 MB for busy realms). Use streaming if needed.
- **Update frequency:** Approximately hourly snapshots
- **Namespace:** `dynamic-us` (changes per-hour, unlike `profile-us` which is more static)
- **Commodities:** Separate endpoint since patch 9.2.7: `GET /data/wow/auctions/commodities`
  - Region-wide (not per connected realm)
  - Most consumables/mats are commodities

### API Calls Per Hour

- 1 auctions endpoint call per hour (or 1 commodities + 1 connected-realm auctions)
- **2 calls/hour = 48 calls/day** — negligible impact on rate limits

---

## Tests

- Unit test `copper_to_gold_str()` with various values
- Unit test `sync_ah_prices()` with mock Blizzard response
- Unit test price filtering (only tracked items stored)
- Unit test `get_price_change()` calculation
- Unit test `cleanup_old_prices()` retention logic
- Unit test connected realm resolution
- Integration test: full AH sync pipeline with mock data
- Verify index page market watch card renders
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0035 (tracked_items, item_price_history, site_config column)
- [ ] ORM models
- [ ] `get_connected_realm_id()` method on BlizzardClient
- [ ] `get_auctions()` + `get_commodities()` methods on BlizzardClient
- [ ] `ah_sync.py` (sync + cleanup)
- [ ] `ah_service.py` (price helpers, queries)
- [ ] Scheduler: hourly AH pipeline
- [ ] `copper_to_gold_str()` Jinja2 filter
- [ ] Index page Market Watch card
- [ ] Admin page: `/admin/ah-pricing`
- [ ] Add/remove tracked items
- [ ] Seed common consumables
- [ ] Tests
