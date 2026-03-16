"""
Auction House price sync.

Fetches AH snapshots from Blizzard (commodities + connected-realm),
filters to tracked items, computes price statistics, and stores in
item_price_history.

Most guild-tracked items (flasks, enchants, gems) are region-wide commodities.
The sync checks the commodities endpoint first, then falls back to the
connected-realm auctions endpoint for any items not found there.
"""

import logging
import statistics
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


async def sync_ah_prices(pool: asyncpg.Pool, blizzard_client, connected_realm_id: int) -> dict:
    """
    Fetch AH snapshots and store prices for tracked items.

    Tries the commodities endpoint first (most tracked items are commodities),
    then falls back to the connected-realm auctions endpoint for anything not found.

    Returns a stats dict with items_updated and items_not_listed counts.
    """
    # 1. Load active tracked items
    async with pool.acquire() as conn:
        tracked = await conn.fetch(
            "SELECT id, item_id FROM guild_identity.tracked_items WHERE is_active = TRUE"
        )
    tracked_map: dict[int, int] = {row["item_id"]: row["id"] for row in tracked}
    if not tracked_map:
        return {"status": "no_tracked_items", "items_updated": 0, "items_not_listed": 0}

    # Per-item aggregation: item_id → list of unit prices, qty, auction count
    item_prices: dict[int, list[int]] = {}
    item_quantities: dict[int, int] = {}
    item_auction_counts: dict[int, int] = {}

    # 2. Commodities endpoint (region-wide — covers most tracked items)
    try:
        commodities = await blizzard_client.get_commodities()
        if commodities:
            _aggregate_auctions(
                commodities.get("auctions", []),
                tracked_map,
                item_prices,
                item_quantities,
                item_auction_counts,
            )
            logger.info(
                "AH sync: commodities fetched, found %d tracked items in %d total auctions",
                len(item_prices),
                len(commodities.get("auctions", [])),
            )
    except Exception as exc:
        logger.warning("AH sync: commodities fetch failed (non-fatal): %s", exc)

    # 3. Connected-realm auctions for any items not found in commodities
    missing = [iid for iid in tracked_map if iid not in item_prices]
    if missing:
        try:
            realm_data = await blizzard_client.get_auctions(connected_realm_id)
            if realm_data:
                _aggregate_auctions(
                    realm_data.get("auctions", []),
                    {iid: tracked_map[iid] for iid in missing},
                    item_prices,
                    item_quantities,
                    item_auction_counts,
                )
                logger.info(
                    "AH sync: realm auctions fetched for %d missing items", len(missing)
                )
        except Exception as exc:
            logger.warning("AH sync: realm auctions fetch failed (non-fatal): %s", exc)

    # 4. Compute stats and upsert
    now = datetime.now(timezone.utc)
    items_updated = 0
    items_not_listed = 0

    async with pool.acquire() as conn:
        for item_id, prices in item_prices.items():
            tracked_item_id = tracked_map[item_id]
            await conn.execute(
                """
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
            items_updated += 1

        items_not_listed = sum(1 for iid in tracked_map if iid not in item_prices)

    logger.info(
        "AH sync complete: updated=%d not_listed=%d",
        items_updated,
        items_not_listed,
    )
    return {
        "status": "ok",
        "items_updated": items_updated,
        "items_not_listed": items_not_listed,
    }


def _aggregate_auctions(
    auctions: list[dict],
    tracked_map: dict[int, int],
    item_prices: dict[int, list[int]],
    item_quantities: dict[int, int],
    item_auction_counts: dict[int, int],
) -> None:
    """Accumulate price/qty data from a raw auctions list into the aggregation dicts."""
    for auction in auctions:
        item_id = auction.get("item", {}).get("id") if isinstance(auction.get("item"), dict) else auction.get("item_id")
        if item_id not in tracked_map:
            continue

        # unit_price for commodities, buyout for non-commodities
        price = auction.get("unit_price") or auction.get("buyout", 0)
        qty = auction.get("quantity", 1)

        if not price or price <= 0:
            continue

        item_prices.setdefault(item_id, []).append(price)
        item_quantities[item_id] = item_quantities.get(item_id, 0) + qty
        item_auction_counts[item_id] = item_auction_counts.get(item_id, 0) + 1


async def cleanup_old_prices(pool: asyncpg.Pool, days_hourly: int = 30, days_daily: int = 180) -> dict:
    """
    Retain hourly data for 30 days, then thin to one snapshot per day.
    Delete everything older than 180 days.

    Returns counts of deleted rows.
    """
    async with pool.acquire() as conn:
        # Delete hourly data older than 30 days, keeping one row per item per day
        deleted_hourly = await conn.fetchval(
            """
            WITH to_delete AS (
                SELECT id FROM guild_identity.item_price_history
                WHERE snapshot_at < NOW() - ($1 * INTERVAL '1 day')
                  AND id NOT IN (
                      SELECT DISTINCT ON (tracked_item_id, snapshot_at::date) id
                      FROM guild_identity.item_price_history
                      WHERE snapshot_at < NOW() - ($1 * INTERVAL '1 day')
                      ORDER BY tracked_item_id, snapshot_at::date, snapshot_at ASC
                  )
            )
            DELETE FROM guild_identity.item_price_history
            WHERE id IN (SELECT id FROM to_delete)
            RETURNING id
            """,
            days_hourly,
        )

        # Delete everything older than 180 days
        deleted_old = await conn.fetchval(
            """
            WITH to_delete AS (
                SELECT id FROM guild_identity.item_price_history
                WHERE snapshot_at < NOW() - ($1 * INTERVAL '1 day')
            )
            DELETE FROM guild_identity.item_price_history
            WHERE id IN (SELECT id FROM to_delete)
            RETURNING id
            """,
            days_daily,
        )

    hourly_count = len(deleted_hourly) if deleted_hourly else 0
    old_count = len(deleted_old) if deleted_old else 0
    logger.info("AH cleanup: removed %d hourly rows, %d old rows", hourly_count, old_count)
    return {"deleted_hourly": hourly_count, "deleted_old": old_count}
