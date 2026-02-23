"""
Retroactive provisioning script — Phase 2.6

Provisions all existing guild members from guild_identity into common.*
without sending any DMs, invites, or Discord role changes.

Use this to clean up the roster before enabling live onboarding for new members.

Usage:
    python scripts/retroactive_provision.py [--dry-run] [--person-id N]

Options:
    --dry-run     Print what would happen without writing to the database
    --person-id N Only process a single person (by guild_identity.persons.id)
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from dotenv import load_dotenv
load_dotenv(repo_root / ".env")

import asyncpg

from sv_common.guild_sync.onboarding.provisioner import AutoProvisioner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retroactive_provision")


async def get_all_person_ids(pool: asyncpg.Pool) -> list[int]:
    """Return all person IDs that have at least one character or discord member."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT p.id
               FROM guild_identity.persons p
               WHERE EXISTS (
                   SELECT 1 FROM guild_identity.wow_characters wc
                   WHERE wc.person_id = p.id AND wc.removed_at IS NULL
               )
               OR EXISTS (
                   SELECT 1 FROM guild_identity.discord_members dm
                   WHERE dm.person_id = p.id AND dm.is_present = TRUE
               )
               ORDER BY p.id"""
        )
    return [r["id"] for r in rows]


async def main():
    parser = argparse.ArgumentParser(description="Retroactively provision existing guild members")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no DB writes")
    parser.add_argument("--person-id", type=int, help="Only process this person ID")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        logger.error("DATABASE_URL not set in environment / .env")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url)

    if args.person_id:
        person_ids = [args.person_id]
    else:
        person_ids = await get_all_person_ids(pool)

    logger.info("Found %d persons to process", len(person_ids))

    if args.dry_run:
        logger.info("DRY RUN — no changes will be written")
        logger.info("Would provision person IDs: %s", person_ids)
        await pool.close()
        return

    provisioner = AutoProvisioner(db_pool=pool, bot=None)  # bot=None → no DMs, no role changes

    totals = {
        "ok": 0,
        "skipped": 0,
        "errors": 0,
        "chars_created": 0,
        "chars_skipped": 0,
        "discord_linked": 0,
    }

    for i, person_id in enumerate(person_ids, 1):
        try:
            result = await provisioner.provision_person(
                person_id,
                silent=True,              # ← No DMs, no Discord roles, no invite codes
                onboarding_session_id=None,
            )
            if result["errors"]:
                logger.warning("person=%d skipped: %s", person_id, result["errors"])
                totals["skipped"] += 1
            else:
                totals["ok"] += 1
                totals["chars_created"] += result["characters_created"]
                totals["chars_skipped"] += result["characters_skipped"]
                if result["discord_linked"]:
                    totals["discord_linked"] += 1

            if i % 10 == 0:
                logger.info("Progress: %d/%d processed", i, len(person_ids))

        except Exception as e:
            logger.error("person=%d failed: %s", person_id, e, exc_info=True)
            totals["errors"] += 1

    await pool.close()

    print("\n" + "=" * 60)
    print("Retroactive provisioning complete")
    print("=" * 60)
    print(f"  Persons processed:     {totals['ok'] + totals['skipped'] + totals['errors']}")
    print(f"  Successfully created:  {totals['ok']}")
    print(f"  Skipped (no data):     {totals['skipped']}")
    print(f"  Errors:                {totals['errors']}")
    print(f"  Characters created:    {totals['chars_created']}")
    print(f"  Characters skipped:    {totals['chars_skipped']}")
    print(f"  Discord accounts linked: {totals['discord_linked']}")
    print("=" * 60)
    print("\nNO DMs were sent. NO Discord roles were assigned.")
    print("NO invite codes were generated.")
    print("Run again with --dry-run to preview results.")


if __name__ == "__main__":
    asyncio.run(main())
