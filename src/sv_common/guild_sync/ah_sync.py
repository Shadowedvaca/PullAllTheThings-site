"""
Auction House price sync.

Fetches AH snapshots from Blizzard (commodities + connected-realm),
filters to tracked items, computes price statistics, and stores in
item_price_history.

Most guild-tracked items (flasks, enchants, gems) are region-wide commodities.
Commodities are stored with connected_realm_id=0 (sentinel for region-wide).
Per-realm auctions are stored with the actual connected_realm_id.
"""

import logging
import statistics
import time
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


async def get_active_connected_realm_ids(pool: asyncpg.Pool, blizzard_client, days: int = 30) -> list[int]:
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


async def sync_ah_prices(pool: asyncpg.Pool, blizzard_client, connected_realm_ids: list[int]) -> dict:
    """
    Fetch AH snapshots and store prices for tracked items across multiple realms.

    Pipeline:
      1. Load active tracked items.
      2. Fetch commodities (region-wide) — store with connected_realm_id=0.
      3. For each connected_realm_id in connected_realm_ids, fetch realm auctions
         and store items found there with that realm's ID.

    Returns a stats dict with items_updated, items_not_listed, realms_synced counts.
    """
    # 1. Load active tracked items
    async with pool.acquire() as conn:
        tracked = await conn.fetch(
            "SELECT id, item_id FROM guild_identity.tracked_items WHERE is_active = TRUE"
        )
    tracked_map: dict[int, int] = {row["item_id"]: row["id"] for row in tracked}
    if not tracked_map:
        return {"status": "no_tracked_items", "items_updated": 0, "items_not_listed": 0, "realms_synced": 0}

    now = datetime.now(timezone.utc)
    items_updated = 0

    # 2. Commodities (region-wide) — stored with connected_realm_id=0
    commodity_item_prices: dict[int, list[int]] = {}
    commodity_quantities: dict[int, int] = {}
    commodity_auction_counts: dict[int, int] = {}

    try:
        commodities = await blizzard_client.get_commodities()
        if commodities:
            _aggregate_auctions(
                commodities.get("auctions", []),
                tracked_map,
                commodity_item_prices,
                commodity_quantities,
                commodity_auction_counts,
            )
            logger.info(
                "AH sync: commodities fetched, found %d tracked items in %d total auctions",
                len(commodity_item_prices),
                len(commodities.get("auctions", [])),
            )
    except Exception as exc:
        logger.warning("AH sync: commodities fetch failed (non-fatal): %s", exc)

    # Store commodity prices with realm_id=0
    if commodity_item_prices:
        async with pool.acquire() as conn:
            for item_id, prices in commodity_item_prices.items():
                tracked_item_id = tracked_map[item_id]
                await conn.execute(
                    """
                    INSERT INTO guild_identity.item_price_history
                        (tracked_item_id, snapshot_at, min_buyout, median_price,
                         mean_price, quantity_available, num_auctions, connected_realm_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 0)
                    ON CONFLICT (tracked_item_id, snapshot_at, connected_realm_id) DO UPDATE SET
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
                    commodity_quantities.get(item_id, 0),
                    commodity_auction_counts.get(item_id, 0),
                )
                items_updated += 1

    # Items not found in commodities — candidates for realm auction lookup
    not_in_commodities = [iid for iid in tracked_map if iid not in commodity_item_prices]

    # 3. Per-realm auctions — for items not found in commodities
    realms_synced = 0
    realm_found_items: set[int] = set()
    for realm_id in connected_realm_ids:
        if not not_in_commodities:
            break  # All items found in commodities
        realm_prices: dict[int, list[int]] = {}
        realm_quantities: dict[int, int] = {}
        realm_auction_counts: dict[int, int] = {}

        try:
            realm_data = await blizzard_client.get_auctions(realm_id)
            if realm_data:
                _aggregate_auctions(
                    realm_data.get("auctions", []),
                    {iid: tracked_map[iid] for iid in not_in_commodities},
                    realm_prices,
                    realm_quantities,
                    realm_auction_counts,
                )
                logger.info(
                    "AH sync: realm %d auctions — found %d of %d missing items",
                    realm_id, len(realm_prices), len(not_in_commodities),
                )
            realms_synced += 1
        except Exception as exc:
            logger.warning("AH sync: realm %d auctions fetch failed (non-fatal): %s", realm_id, exc)
            continue

        if realm_prices:
            async with pool.acquire() as conn:
                for item_id, prices in realm_prices.items():
                    tracked_item_id = tracked_map[item_id]
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.item_price_history
                            (tracked_item_id, snapshot_at, min_buyout, median_price,
                             mean_price, quantity_available, num_auctions, connected_realm_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (tracked_item_id, snapshot_at, connected_realm_id) DO UPDATE SET
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
                        realm_quantities.get(item_id, 0),
                        realm_auction_counts.get(item_id, 0),
                        realm_id,
                    )
                    items_updated += 1
                    realm_found_items.add(item_id)

    items_not_listed = sum(
        1 for iid in tracked_map
        if iid not in commodity_item_prices and iid not in realm_found_items
    )

    logger.info(
        "AH sync complete: updated=%d items_not_listed=%d realms_synced=%d",
        items_updated,
        items_not_listed,
        realms_synced,
    )
    return {
        "status": "ok",
        "items_updated": items_updated,
        "items_not_listed": items_not_listed,
        "realms_synced": realms_synced,
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
        deleted_hourly = await conn.fetch(
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
        deleted_old = await conn.fetch(
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
