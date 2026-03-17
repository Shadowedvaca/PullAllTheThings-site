"""
Auction House price service.

Provides display helpers and query functions for AH price data.
"""

import asyncpg


def copper_to_gold_str(copper: int | None) -> str:
    """Convert a copper value to WoW gold display format (e.g. '1,234g 56s')."""
    if copper is None or copper <= 0:
        return "—"
    gold = copper // 10000
    silver = (copper % 10000) // 100
    if gold >= 1000:
        return f"{gold:,}g"
    elif gold > 0:
        return f"{gold}g {silver}s"
    else:
        return f"{silver}s"


async def get_current_prices(pool: asyncpg.Pool) -> list[dict]:
    """Return the latest price snapshot for every active tracked item."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ti.id)
                ti.id, ti.item_id, ti.item_name, ti.category, ti.display_order,
                iph.min_buyout, iph.median_price, iph.quantity_available,
                iph.num_auctions, iph.snapshot_at
            FROM guild_identity.tracked_items ti
            LEFT JOIN guild_identity.item_price_history iph
                ON iph.tracked_item_id = ti.id
            WHERE ti.is_active = TRUE
            ORDER BY ti.id, iph.snapshot_at DESC NULLS LAST
            """
        )
    return [dict(r) for r in rows]


async def get_prices_for_realm(pool: asyncpg.Pool, connected_realm_id: int) -> list[dict]:
    """
    Latest price snapshot per active tracked item, merging:
      - connected_realm_id = 0 (commodity baseline, region-wide)
      - connected_realm_id = <realm> (realm-specific override, if exists)

    Prefers realm row when both exist. Sets is_realm_specific flag.
    Falls back to commodity data if realm-specific not available.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ti.id)
                ti.id, ti.item_id, ti.item_name, ti.category, ti.display_order,
                iph.min_buyout, iph.median_price, iph.quantity_available,
                iph.num_auctions, iph.snapshot_at, iph.connected_realm_id,
                (iph.connected_realm_id IS NOT NULL AND iph.connected_realm_id != 0) AS is_realm_specific
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


async def get_available_realms(pool: asyncpg.Pool) -> list[dict]:
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


async def get_tracked_items_with_prices(pool: asyncpg.Pool) -> list[dict]:
    """Return tracked items with latest prices and 24h change for admin display."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ti.id, ti.item_id, ti.item_name, ti.category, ti.display_order,
                ti.is_active, ti.created_at,
                iph.min_buyout, iph.median_price, iph.quantity_available,
                iph.num_auctions, iph.snapshot_at
            FROM guild_identity.tracked_items ti
            LEFT JOIN LATERAL (
                SELECT min_buyout, median_price, quantity_available, num_auctions, snapshot_at
                FROM guild_identity.item_price_history
                WHERE tracked_item_id = ti.id
                ORDER BY snapshot_at DESC
                LIMIT 1
            ) iph ON TRUE
            ORDER BY ti.display_order, ti.item_name
            """
        )
    items = [dict(r) for r in rows]

    # Fetch 24h-ago prices in a single query
    async with pool.acquire() as conn:
        prev_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (tracked_item_id)
                tracked_item_id, min_buyout
            FROM guild_identity.item_price_history
            WHERE snapshot_at <= NOW() - INTERVAL '24 hours'
            ORDER BY tracked_item_id, snapshot_at DESC
            """
        )
    prev_map = {r["tracked_item_id"]: r["min_buyout"] for r in prev_rows}

    for item in items:
        prev = prev_map.get(item["id"])
        current = item.get("min_buyout")
        if current and prev and prev > 0:
            item["change_pct"] = round(((current - prev) / prev) * 100, 1)
        else:
            item["change_pct"] = None

    return items


async def get_price_trend(pool: asyncpg.Pool, tracked_item_id: int, days: int = 7) -> list[dict]:
    """Return price history for charting (last N days, ascending by time)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT snapshot_at, min_buyout, median_price, quantity_available
            FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1
              AND snapshot_at >= NOW() - ($2 * INTERVAL '1 day')
            ORDER BY snapshot_at ASC
            """,
            tracked_item_id,
            days,
        )
    return [dict(r) for r in rows]


async def get_price_change(pool: asyncpg.Pool, tracked_item_id: int) -> dict:
    """Return current price and pct change vs 24h ago for a single item."""
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            """
            SELECT min_buyout FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1
            ORDER BY snapshot_at DESC LIMIT 1
            """,
            tracked_item_id,
        )
        yesterday = await conn.fetchval(
            """
            SELECT min_buyout FROM guild_identity.item_price_history
            WHERE tracked_item_id = $1
              AND snapshot_at <= NOW() - INTERVAL '24 hours'
            ORDER BY snapshot_at DESC LIMIT 1
            """,
            tracked_item_id,
        )
    if current and yesterday and yesterday > 0:
        change_pct = ((current - yesterday) / yesterday) * 100
        return {"current": current, "yesterday": yesterday, "change_pct": round(change_pct, 1)}
    return {"current": current, "yesterday": None, "change_pct": None}


async def get_consumable_prices_for_realm(
    pool: asyncpg.Pool, connected_realm_id: int
) -> list[dict]:
    """
    Return consumable/material prices with 24h change for the crafting panel.

    Filters to active tracked items with category in ('consumable', 'material').
    Merges commodity (realm_id=0) + realm-specific data, prefers realm-specific.
    """
    from urllib.parse import quote_plus

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ti.id)
                ti.id, ti.item_name, ti.category,
                iph.min_buyout, iph.quantity_available,
                iph.connected_realm_id
            FROM guild_identity.tracked_items ti
            LEFT JOIN guild_identity.item_price_history iph
                ON iph.tracked_item_id = ti.id
               AND iph.connected_realm_id IN (0, $1)
               AND iph.snapshot_at >= NOW() - INTERVAL '2 hours'
            WHERE ti.is_active = TRUE
              AND ti.category IN ('consumable', 'material')
            ORDER BY ti.id,
                     (iph.connected_realm_id = $1) DESC NULLS LAST,
                     iph.snapshot_at DESC NULLS LAST
            """,
            connected_realm_id,
        )

        if not rows:
            return []

        item_ids = [r["id"] for r in rows]

        prev_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (tracked_item_id)
                tracked_item_id, min_buyout
            FROM guild_identity.item_price_history
            WHERE tracked_item_id = ANY($1::int[])
              AND connected_realm_id IN (0, $2)
              AND snapshot_at <= NOW() - INTERVAL '24 hours'
            ORDER BY tracked_item_id, snapshot_at DESC
            """,
            item_ids,
            connected_realm_id,
        )

    prev_map = {r["tracked_item_id"]: r["min_buyout"] for r in prev_rows}

    result = []
    for r in rows:
        item_id = r["id"]
        current = r["min_buyout"]
        prev = prev_map.get(item_id)
        change_pct = None
        if current and prev and prev > 0:
            change_pct = round(((current - prev) / prev) * 100, 1)

        name = r["item_name"]
        wowhead_url = f"https://www.wowhead.com/search?q={quote_plus(name)}"

        result.append({
            "tracked_item_id": item_id,
            "item_name": name,
            "category": r["category"],
            "min_buyout": current,
            "min_buyout_display": copper_to_gold_str(current),
            "change_pct": change_pct,
            "quantity_available": r["quantity_available"],
            "wowhead_url": wowhead_url,
        })

    return result
