# Phase 5.3 — My Characters: Realm-Aware Market Panel

## Goal

Integrate the multi-realm AH pricing work (originally Phase 4.6.1) into the My
Characters dashboard as a personalized Market panel. Each character shows AH prices
for their connected realm (with commodity fallback), making the data feel personal.
Also upgrades the public index page Market Watch to support realm switching.

This phase implements the full multi-realm collection backend AND the character-level
market panel in one go.

---

## Background

Phase 4.6 collects AH prices for a single connected realm (the guild's home realm,
stored in `site_config.connected_realm_id`). This works for commodity items (flasks,
enchants, gems) because those are region-wide. But non-commodity items (some mats,
gear) vary by realm. More importantly, members on other realms want to see their realm.

The sentinel convention: `connected_realm_id = 0` = region-wide commodity.

---

## Prerequisites

- Phase 4.6 complete (`tracked_items`, `item_price_history`, `sync_ah_prices()` running)
- Phase 5.0 complete (My Characters page foundation)
- `item_price_history.connected_realm_id` column exists (Phase 4.6)

---

## Database Migration: 0041_ah_multi_realm

```sql
ALTER TABLE common.site_config
    ADD COLUMN active_connected_realm_ids INTEGER[] DEFAULT '{}';
```

Caches the list of connected realm IDs with active members so `run_ah_sync()` doesn't
re-resolve slugs → realm IDs every hour.

---

## Task 1: Active Realm Resolution

### File: `src/sv_common/guild_sync/ah_sync.py`

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

Refresh list once daily (first sync of each day or a Sunday sweep). Store result in
`site_config.active_connected_realm_ids` between runs.

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
3. **Realm auctions** — for each `connected_realm_id` in `connected_realm_ids`, fetch
   realm auctions, store found items with that realm's ID.
4. Return stats: `items_updated`, `items_not_listed`, `realms_synced`.

### File: `src/sv_common/guild_sync/scheduler.py` — update `run_ah_sync()`

```python
async def run_ah_sync(self):
    # 1. Load active_connected_realm_ids from site_config
    # 2. If empty or daily refresh window:
    #    call get_active_connected_realm_ids(), store to site_config
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

## Task 3: Realm-Aware Price Query

### File: `src/sv_common/guild_sync/ah_service.py`

```python
async def get_prices_for_realm(pool, connected_realm_id: int) -> list[dict]:
    """
    Latest price snapshot per active tracked item, merging:
      - connected_realm_id = 0 (commodity baseline)
      - connected_realm_id = <realm> (realm-specific override, if exists)

    Prefer realm row when both exist. Set is_realm_specific flag.
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
                     (iph.connected_realm_id = $1) DESC NULLS LAST,
                     iph.snapshot_at DESC NULLS LAST
            """,
            connected_realm_id,
        )
    return [dict(r) for r in rows]


async def get_available_realms(pool) -> list[dict]:
    """
    Connected realms with recent price data, for the realm dropdown.
    Returns [{connected_realm_id, label}].
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
    return [
        {
            "connected_realm_id": r["connected_realm_id"],
            "label": "Region (US)" if r["connected_realm_id"] == 0
                     else f"Realm #{r['connected_realm_id']}",
        }
        for r in rows
    ]
```

---

## Task 4: Public API — Realm-Aware Prices

### New endpoint: `GET /api/v1/guild/ah-prices?realm_id=<id>`

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

## Task 5: My Characters — Market Panel

Added to the My Characters dashboard for the selected character.

Shows prices for the character's connected realm (resolved from `realm_slug` via
`get_connected_realm_id()`). Falls back to `site_config.connected_realm_id` if
the character's realm can't be resolved, then to 0 (commodity-only).

Layout matches the public Market Watch table but is embedded in the dashboard card.
No realm switcher here — the character's realm is the context. The switcher lives on
the public index page.

---

## Task 6: Index Page — Market Watch Upgrades

### Realm switcher (above table, only if >1 realm has data):

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

### Table row highlight for realm-specific prices:

```html
<tr class="{% if item.is_realm_specific %}lp-market-row--realm{% endif %}">
    ...
    {% if item.is_realm_specific %}<span class="lp-market-realm-flag">*</span>{% endif %}
</tr>
```

### Footnote (only when realm-specific rows exist):

```html
{% if ah_prices | selectattr('is_realm_specific') | list %}
<p class="lp-market-footnote">* Realm-specific auction price for your connected realm.</p>
{% endif %}
```

### JS realm switcher:

```javascript
async function switchMarketRealm(realmId) {
    const res = await fetch(`/api/v1/guild/ah-prices?realm_id=${realmId}`);
    const json = await res.json();
    if (!json.ok) return;
    renderMarketTable(json.data.prices);
}
```

### CSS additions to `landing.css`:

```css
.lp-market-row--realm { background: rgba(212, 168, 75, 0.05); }
.lp-market-realm-flag { color: var(--color-accent); font-weight: 700; margin-left: 0.2rem; }
.lp-market-realm-bar {
    display: flex; align-items: center; gap: 0.5rem;
    font-size: 0.82rem; color: var(--color-text-muted); margin-bottom: 0.5rem;
}
.lp-market-realm-bar select {
    background: var(--color-card); border: 1px solid var(--color-border);
    color: var(--color-text); padding: 0.2rem 0.4rem;
    border-radius: 4px; font-size: 0.82rem;
}
.lp-market-footnote {
    font-size: 0.72rem; color: var(--color-text-muted);
    padding: 0.35rem 0.75rem; border-top: 1px solid var(--color-border); margin: 0;
}
```

### `public_pages.py` update (index page):

1. Determine viewer's connected realm:
   - Logged in → resolve their main character's `realm_slug` to connected realm ID.
   - Else → use `site_config.connected_realm_id`.
   - Fallback → 0 (commodity only).
2. Call `get_prices_for_realm(pool, viewer_realm_id)` instead of `get_current_prices()`.
3. Pass `viewer_realm_id` and `available_realms` to template context.

---

## Task 7: Admin Page Updates

Minor updates to `/admin/ah-pricing`:
- Status card: show count of active realms being tracked.
- Items table: add "Realm" column (`connected_realm_id`, labeled "Regional" for 0).
- Force sync accepts optional `realm_id` param to sync one realm.

---

## Tests

- `get_prices_for_realm()` — commodity-only, realm-only, merged cases
- `get_prices_for_realm()` — realm row preferred when both exist
- `get_active_connected_realm_ids()` — filters by last_login cutoff
- `sync_ah_prices()` — commodities stored as `realm_id=0`, realm auctions with actual ID
- `get_available_realms()` — distinct realm IDs from recent history
- `is_realm_specific` flag correct in merged results
- Index page template receives `viewer_realm_id` and `available_realms`
- Market panel on My Characters resolves character's realm correctly

---

## Deliverables Checklist

- [ ] Migration 0041 (`active_connected_realm_ids` on `site_config`)
- [ ] `get_active_connected_realm_ids()` in `ah_sync.py`
- [ ] Updated `sync_ah_prices()` — commodities as realm_id=0, per-realm auctions
- [ ] Updated `run_ah_sync()` — resolves active realms, passes list to sync
- [ ] `get_prices_for_realm(pool, realm_id)` in `ah_service.py`
- [ ] `get_available_realms(pool)` in `ah_service.py`
- [ ] `GET /api/v1/guild/ah-prices?realm_id=<id>` public API endpoint
- [ ] `public_pages.py` — resolve viewer realm, call `get_prices_for_realm()`
- [ ] `index.html` — realm switcher, `lp-market-row--realm`, footnote, JS swap
- [ ] `landing.css` — realm bar, highlight, footnote styles
- [ ] My Characters market panel showing character's realm prices
- [ ] Admin `/admin/ah-pricing` minor updates (realm column, active realm count)
- [ ] Tests
