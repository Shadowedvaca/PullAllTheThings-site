# Phase 4.6.1 — AH Pricing: Multi-Realm Collection & Display

## Goal

Expand AH pricing to collect data for every connected realm where active guild
members have characters, then display it in a way that feels personalized to each
viewer without overwhelming them.

---

## Background / Design Rationale

Most tracked items (flasks, enchants, gems) are **region-wide commodities** —
one price for all US realms, fetched from a single API endpoint. Per-realm
auctions only differ for non-commodity items (gear, some mats).

This distinction is a **back-end detail**. The front end does not need to explain
it. Instead:

- Show the user a clean, familiar item list filtered to their realm context.
- If a realm-specific price exists, highlight it lightly and footnote it once.
- Let users switch realms via a dropdown to compare prices.
- Most users see their realm, feel it's customized, and move on happy.

---

## Prerequisites

- Phase 4.6 complete (tracked_items, item_price_history, scheduler running)
- `item_price_history.connected_realm_id` already exists

---

## Database Migration: 0041_ah_multi_realm

### Sentinel value convention

`connected_realm_id = 0` → region-wide commodity price (no migration needed —
just a code convention). Already supported by the existing column and schema.

### No new tables needed.

The existing `item_price_history` table handles both:
- `connected_realm_id = 0` — commodity / region-wide
- `connected_realm_id = <id>` — realm-specific auction

---

## Task 1: Active Realm Resolution

### File: `src/sv_common/guild_sync/ah_sync.py`

New function:

```python
async def get_active_connected_realm_ids(pool, blizzard_client, days: int = 30) -> list[int]:
    """
    Return connected realm IDs for realms where any guild character
    has logged in within the last `days` days.

    Steps:
      1. Query wow_characters for distinct realm_slugs with recent last_login.
      2. Resolve each slug to a connected realm ID via blizzard_client.
      3. Deduplicate (multiple slugs may share a connected realm).
      4. Return sorted list of connected_realm_ids.
    """
    cutoff_ms = int((time.time() - days * 86400) * 1000)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT realm_slug
            FROM guild_identity.wow_characters
            WHERE last_login_timestamp >= $1
              AND removed_at IS NULL
              AND realm_slug IS NOT NULL
            """,
            cutoff_ms,
        )
    slugs = [r["realm_slug"] for r in rows]
    if not slugs:
        return []

    connected_ids: set[int] = set()
    for slug in slugs:
        crid = await blizzard_client.get_connected_realm_id(slug)
        if crid:
            connected_ids.add(crid)

    return sorted(connected_ids)
```

### Caching active realms

Store the resolved list in a new `site_config` column so we don't re-resolve
every sync run:

```sql
ALTER TABLE common.site_config
    ADD COLUMN active_connected_realm_ids INTEGER[] DEFAULT '{}';
```

Refresh this list once daily (Sunday sweep or first sync of each day).

---

## Task 2: Updated Sync Pipeline

### File: `src/sv_common/guild_sync/ah_sync.py` — update `sync_ah_prices()`

Change signature:

```python
async def sync_ah_prices(pool, blizzard_client, connected_realm_ids: list[int]) -> dict:
```

Pipeline:

1. Load active tracked items.
2. **Commodities** (region-wide) — fetch once, store with `connected_realm_id = 0`.
3. **Realm auctions** — for each `connected_realm_id` in `connected_realm_ids`,
   fetch realm auctions, store found items with that realm's ID.
   - Skip if the item was already found in commodities (it won't appear in realm
     auctions for commodity items anyway).
4. Return stats: `items_updated`, `items_not_listed`, `realms_synced`.

### File: `src/sv_common/guild_sync/scheduler.py` — update `run_ah_sync()`

```python
async def run_ah_sync(self):
    # 1. Load active_connected_realm_ids from site_config
    # 2. If empty or daily refresh window: call get_active_connected_realm_ids(),
    #    store result back to site_config.active_connected_realm_ids
    # 3. Call sync_ah_prices(pool, blizzard_client, connected_realm_ids)
    # 4. Daily: call cleanup_old_prices()
```

### API call budget (example: 8 active realms)

| Call | Count/hour |
|------|-----------|
| Commodities (region-wide) | 1 |
| Realm auctions × 8 | 8 |
| **Total** | **9** |

9 calls/hour vs. 36,000/hour limit — negligible.

---

## Task 3: Price Query — Realm-Aware

### File: `src/sv_common/guild_sync/ah_service.py`

New function (replaces `get_current_prices`):

```python
async def get_prices_for_realm(pool, connected_realm_id: int) -> list[dict]:
    """
    Return latest price snapshot for each active tracked item, merging:
      - connected_realm_id = 0 (commodity / region-wide baseline)
      - connected_realm_id = <realm> (realm-specific override, if it exists)

    Each row includes:
      - All standard price fields
      - is_realm_specific: bool (True if the row came from the realm auction,
        False if it came from commodities)

    Join logic:
      Show items where connected_realm_id IN (0, <realm_id>).
      If BOTH a commodity row (0) and a realm row (<id>) exist for the same
      tracked_item_id, prefer the realm row and set is_realm_specific = True.
      If only the commodity row exists, is_realm_specific = False.
      If only the realm row exists (unusual), show it and set is_realm_specific = True.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ti.id)
                ti.id, ti.item_id, ti.item_name, ti.category, ti.display_order,
                iph.min_buyout, iph.median_price, iph.quantity_available,
                iph.num_auctions, iph.snapshot_at, iph.connected_realm_id,
                (iph.connected_realm_id != 0) AS is_realm_specific
            FROM guild_identity.tracked_items ti
            LEFT JOIN guild_identity.item_price_history iph
                ON iph.tracked_item_id = ti.id
               AND iph.connected_realm_id IN (0, $1)
               AND iph.snapshot_at >= NOW() - INTERVAL '2 hours'
            WHERE ti.is_active = TRUE
            ORDER BY ti.id,
                     -- Prefer realm-specific rows over commodity rows
                     (iph.connected_realm_id = $1) DESC NULLS LAST,
                     iph.snapshot_at DESC NULLS LAST
            """,
            connected_realm_id,
        )
    return [dict(r) for r in rows]
```

Also add:

```python
async def get_available_realms(pool) -> list[dict]:
    """
    Return list of connected realms that have recent price data,
    for populating the realm dropdown.

    Returns: [{connected_realm_id, realm_label}]
    - connected_realm_id = 0 → label "Region (US)"
    - other IDs → label "Realm #{id}" (can be enriched with realm names later)
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT connected_realm_id
            FROM guild_identity.item_price_history
            WHERE snapshot_at >= NOW() - INTERVAL '25 hours'
            ORDER BY connected_realm_id
            """
        )
    realms = []
    for r in rows:
        crid = r["connected_realm_id"]
        realms.append({
            "connected_realm_id": crid,
            "label": "Region (US)" if crid == 0 else f"Realm #{crid}",
        })
    return realms
```

---

## Task 4: Realm Name Lookup (Optional, Phase 4.6.1+)

Blizzard has `GET /data/wow/connected-realm/{id}` which returns a list of
realm slugs + display names for a connected realm group. This could be used to
label the dropdown as "Sen'jin / Echo Isles" instead of "Realm #11".

Store realm display names in `site_config.active_connected_realm_ids` or a
separate lookup table. **Defer to a follow-up patch** — the ID-based dropdown
is functional and acceptable for v1.

---

## Task 5: Public API — Realm-Aware Prices

### New endpoint: `GET /api/v1/guild/ah-prices?realm_id=<id>`

Returns current prices for the given connected realm (merging realm + commodity rows).

```python
@router.get("/guild/ah-prices")
async def get_ah_prices(realm_id: int = 0, request: Request = None):
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "unavailable"}
    prices = await get_prices_for_realm(pool, realm_id)
    realms = await get_available_realms(pool)
    return {"ok": True, "data": {"prices": prices, "available_realms": realms}}
```

---

## Task 6: Front-End Display

### Index page (`/`) — Market Watch card

The index page currently passes `ah_prices` from `get_current_prices()`.

Update `public_pages.py` to:
1. Determine the viewer's connected realm:
   - If logged in → resolve their main character's `realm_slug` to a connected realm ID.
   - Else → use `site_config.connected_realm_id` (guild home realm).
   - Fallback → 0 (commodity prices only).
2. Call `get_prices_for_realm(pool, viewer_realm_id)` instead of `get_current_prices()`.
3. Pass `viewer_realm_id` and `available_realms` to template context.

### index.html — Market Watch card changes

**Realm switcher** (above the table):

```html
{% if available_realms | length > 1 %}
<div class="lp-market-realm-bar">
    <label for="market-realm-select">Showing prices for:</label>
    <select id="market-realm-select" onchange="switchMarketRealm(this.value)">
        {% for realm in available_realms %}
        <option value="{{ realm.connected_realm_id }}"
            {% if realm.connected_realm_id == viewer_realm_id %}selected{% endif %}>
            {{ realm.label }}
        </option>
        {% endfor %}
    </select>
</div>
{% endif %}
```

**Table rows** — add `is-realm-specific` class when `item.is_realm_specific`:

```html
<tr class="{% if item.is_realm_specific %}lp-market-row--realm{% endif %}">
    <td class="lp-market-name">
        <span class="lp-market-cat lp-market-cat--{{ item.category }}">{{ item.category }}</span>
        {{ item.item_name }}
        {% if item.is_realm_specific %}<span class="lp-market-realm-flag">*</span>{% endif %}
    </td>
    ...
</tr>
```

**Footnote** (below table, only shown if any row has `is_realm_specific`):

```html
{% if ah_prices | selectattr('is_realm_specific') | list %}
<p class="lp-market-footnote">* Realm-specific auction price for your connected realm.</p>
{% endif %}
```

**JS realm switcher** — SPA-style swap via the public API:

```javascript
async function switchMarketRealm(realmId) {
    const res = await fetch(`/api/v1/guild/ah-prices?realm_id=${realmId}`);
    const json = await res.json();
    if (!json.ok) return;
    renderMarketTable(json.data.prices);
}
```

### CSS additions to `landing.css`

```css
/* Realm-specific row — lighter background tint */
.lp-market-row--realm {
    background: rgba(212, 168, 75, 0.05);
}
.lp-market-realm-flag {
    color: var(--color-accent);
    font-weight: 700;
    margin-left: 0.2rem;
}
.lp-market-realm-bar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.82rem;
    color: var(--color-text-muted);
    margin-bottom: 0.5rem;
}
.lp-market-realm-bar select {
    background: var(--color-card);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 0.2rem 0.4rem;
    border-radius: 4px;
    font-size: 0.82rem;
}
.lp-market-footnote {
    font-size: 0.72rem;
    color: var(--color-text-muted);
    padding: 0.35rem 0.75rem;
    border-top: 1px solid var(--color-border);
    margin: 0;
}
```

---

## Task 7: Admin page updates (`/admin/ah-pricing`)

Minor updates:
- Status card: show number of active realms being tracked.
- Items table: add a "Realm" column showing which connected_realm_id a price came from (with "Regional" label for 0).
- Force sync now accepts an optional `realm_id` param to sync one realm.

---

## Migration 0041

```sql
ALTER TABLE common.site_config
    ADD COLUMN active_connected_realm_ids INTEGER[] DEFAULT '{}';
```

Stores the cached list of connected realm IDs with active members, so `run_ah_sync()`
doesn't need to re-resolve slugs → realm IDs every hour.

---

## Tests

- Unit: `get_prices_for_realm()` — commodity-only, realm-only, and merged cases
- Unit: `get_prices_for_realm()` — realm row preferred over commodity row when both exist
- Unit: `get_active_connected_realm_ids()` — filters by last_login cutoff
- Unit: `sync_ah_prices()` — stores commodities with realm_id=0, realm auctions with actual ID
- Unit: `get_available_realms()` — returns distinct realm IDs from recent history
- Unit: `is_realm_specific` flag correct in merged results
- Verify index page template receives `viewer_realm_id` and `available_realms`
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0041 (`active_connected_realm_ids` on `site_config`)
- [ ] `get_active_connected_realm_ids()` in `ah_sync.py`
- [ ] Updated `sync_ah_prices()` — commodities stored as realm_id=0, realm auctions per-realm
- [ ] Updated `run_ah_sync()` — resolves active realms, passes list to sync
- [ ] `get_prices_for_realm(pool, realm_id)` in `ah_service.py`
- [ ] `get_available_realms(pool)` in `ah_service.py`
- [ ] `GET /api/v1/guild/ah-prices?realm_id=<id>` public API endpoint
- [ ] `public_pages.py` — resolve viewer realm, call `get_prices_for_realm()`
- [ ] `index.html` — realm switcher dropdown, `lp-market-row--realm` highlight, footnote, JS swap
- [ ] `landing.css` — realm bar, highlight, footnote styles
- [ ] Admin `/admin/ah-pricing` minor updates (realm column, active realm count)
- [ ] Tests
