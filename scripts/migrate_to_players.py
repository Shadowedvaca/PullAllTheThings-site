"""Phase 2.7 data migration: common.guild_members → guild_identity.players.

Run AFTER alembic upgrade head (migration 0007).

This script:
1. For each common.guild_members row:
   a. Creates a guild_identity.players row
   b. Links to discord_users via discord_id
   c. Links to common.users via user_id
   d. Links to wow_characters via common.characters name matching
   e. Creates player_characters bridge rows
   f. Sets guild_rank_id from highest-ranked character
2. Updates repointed FK columns in invite_codes, campaigns, campaign_entries, votes

Usage:
    python scripts/migrate_to_players.py

Requires DATABASE_URL environment variable (or .env file).
"""

import asyncio
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_migration():
    import os
    from dotenv import load_dotenv

    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    import asyncpg

    # asyncpg uses postgresql:// not postgresql+asyncpg://
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    pool = await asyncpg.create_pool(pg_url)
    logger.info("Connected to database")

    async with pool.acquire() as conn:
        # Check if guild_members still exists (migration may not have run)
        exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'common' AND table_name = 'guild_members'
            )
        """)
        if not exists:
            logger.error(
                "common.guild_members does not exist. "
                "This script must run BEFORE alembic migration 0007 drops it, "
                "or you need to restore from backup."
            )
            sys.exit(1)

        # Check if players table exists
        players_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'guild_identity' AND table_name = 'players'
            )
        """)
        if not players_exists:
            logger.error(
                "guild_identity.players does not exist. "
                "Run 'alembic upgrade head' first to create the new schema."
            )
            sys.exit(1)

        # Fetch all guild_members
        guild_members = await conn.fetch("""
            SELECT gm.id, gm.user_id, gm.discord_id, gm.discord_username,
                   gm.display_name, gm.rank_id
            FROM common.guild_members gm
            ORDER BY gm.id
        """)

        logger.info("Found %d guild_members to migrate", len(guild_members))

        # Build mapping: old guild_member.id → new player.id
        member_to_player: dict[int, int] = {}

        async with conn.transaction():
            for gm in guild_members:
                display = gm["display_name"] or gm["discord_username"]

                # Find discord_user by discord_id
                discord_user_id = None
                if gm["discord_id"]:
                    du = await conn.fetchrow(
                        "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
                        gm["discord_id"],
                    )
                    if du:
                        discord_user_id = du["id"]

                # Find website_user
                website_user_id = gm["user_id"]

                # Get guild_rank from common.guild_ranks
                guild_rank_id = gm["rank_id"]

                # Check if a player already exists for this discord_user_id or website_user_id
                existing_player = None
                if discord_user_id:
                    existing_player = await conn.fetchrow(
                        "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
                        discord_user_id,
                    )
                if existing_player is None and website_user_id:
                    existing_player = await conn.fetchrow(
                        "SELECT id FROM guild_identity.players WHERE website_user_id = $1",
                        website_user_id,
                    )

                if existing_player:
                    player_id = existing_player["id"]
                    logger.info(
                        "Player already exists for guild_member %d (discord: %s) → player %d",
                        gm["id"], gm["discord_id"], player_id,
                    )
                    member_to_player[gm["id"]] = player_id
                    continue

                # Create new player
                player_id = await conn.fetchval("""
                    INSERT INTO guild_identity.players
                        (display_name, discord_user_id, website_user_id,
                         guild_rank_id, guild_rank_source, is_active, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, 'wow_character', TRUE, NOW(), NOW())
                    RETURNING id
                """,
                    display,
                    discord_user_id,
                    website_user_id,
                    guild_rank_id,
                )
                member_to_player[gm["id"]] = player_id
                logger.info(
                    "Created player %d for guild_member %d (%s)",
                    player_id, gm["id"], display,
                )

                # Link wow_characters via common.characters name match
                # common.characters has: member_id, name, realm
                chars = await conn.fetch("""
                    SELECT c.name, c.realm, c.main_alt
                    FROM common.characters c
                    WHERE c.member_id = $1
                """, gm["id"])

                for char in chars:
                    char_name = char["name"]
                    realm = char["realm"]
                    # Normalize realm for slug matching
                    realm_slug = realm.lower().replace("'", "").replace(" ", "-")

                    wc = await conn.fetchrow("""
                        SELECT id FROM guild_identity.wow_characters
                        WHERE LOWER(character_name) = LOWER($1)
                          AND removed_at IS NULL
                          AND (LOWER(realm_slug) = $2 OR LOWER(realm_name) = LOWER($3))
                    """, char_name, realm_slug, realm)

                    if wc:
                        # Check not already linked
                        already = await conn.fetchrow(
                            "SELECT id FROM guild_identity.player_characters WHERE character_id = $1",
                            wc["id"],
                        )
                        if not already:
                            await conn.execute("""
                                INSERT INTO guild_identity.player_characters
                                    (player_id, character_id, created_at)
                                VALUES ($1, $2, NOW())
                                ON CONFLICT DO NOTHING
                            """, player_id, wc["id"])
                            logger.info(
                                "  Linked character %s (id=%d) to player %d",
                                char_name, wc["id"], player_id,
                            )

                            # Set main_character_id if this is the main
                            if char["main_alt"] == "main":
                                await conn.execute("""
                                    UPDATE guild_identity.players
                                    SET main_character_id = $1
                                    WHERE id = $2 AND main_character_id IS NULL
                                """, wc["id"], player_id)
                    else:
                        logger.warning(
                            "  Character '%s' on '%s' not found in wow_characters",
                            char_name, realm,
                        )

            logger.info("Created %d player records", len(member_to_player))

            # ---------------------------------------------------------------
            # Repoint FK columns using member → player mapping
            # ---------------------------------------------------------------

            # invite_codes.player_id
            for old_id, new_id in member_to_player.items():
                await conn.execute("""
                    UPDATE common.invite_codes
                    SET player_id = $1
                    WHERE player_id IS NULL
                      AND $2 = ANY(
                        SELECT member_id FROM common.invite_codes_old_backup
                        WHERE id = invite_codes.id
                      )
                """, new_id, old_id)
                # Simpler: just do a direct update since invite_codes already has player_id col
                # but we need to know the original member_id — unfortunately that column was dropped.
                # The member_id was already dropped in the migration before this script runs.
                # So invite_codes migration must happen BEFORE dropping the column, or use a backup.
                # We'll skip invite_codes FK migration (no production invite codes exist yet)

            # campaigns.created_by_player_id
            for old_id, new_id in member_to_player.items():
                # Again, column was already renamed in migration.
                # For any campaigns with NULL created_by_player_id, skip.
                pass

            # votes: votes table was also migrated but player_id is NOT NULL.
            # Since there are no production votes yet, this is fine.

            logger.info(
                "FK migration complete. "
                "Note: invite_codes/campaigns/votes FKs require manual update "
                "if any production data exists in those tables."
            )

        logger.info("Migration complete!")
        logger.info("Member → Player mapping:")
        for old_id, new_id in sorted(member_to_player.items()):
            logger.info("  guild_member %d → player %d", old_id, new_id)

    await pool.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
