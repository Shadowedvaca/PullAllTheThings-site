#!/usr/bin/env python
"""Setup script for the Salt All The Things Profile Pic Contest campaign.

Run this once against the production database to create the art vote campaign
and all 10 entries. The campaign is created in DRAFT status — Mike activates
it when ready.

Usage:
    python scripts/setup_art_vote.py

Environment:
    DATABASE_URL — must be set (reads from .env if present)

What it does:
    1. Finds or creates the campaign (idempotent — safe to re-run)
    2. Populates 10 entries with Google Drive image URLs
    3. Leaves campaign in DRAFT status for Mike to review and activate

Image URLs follow the pattern:
    https://drive.google.com/uc?id={FILE_ID}&export=view

Mike needs to:
    1. Upload each image to the shared drive
    2. Set sharing to "Anyone with the link can view"
    3. Copy the file ID from the share link URL
    4. Update the FILE_IDS dict below with each file's ID
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION — Update FILE_IDS with Google Drive file IDs before running
# ---------------------------------------------------------------------------

CAMPAIGN_TITLE = "Salt All The Things Profile Pic Contest"

CAMPAIGN_DESCRIPTION = (
    "Vote for your favourite character portrait! "
    "These will be used as profile pictures for the Salt All The Things podcast. "
    "Pick your top 3!"
)

# Duration in hours (168 = 1 week). Mike can change this before activating.
DURATION_HOURS = 168

# Minimum rank to vote (3 = Veteran+)
MIN_RANK_TO_VOTE = 3

# Anyone can view results (None = public)
MIN_RANK_TO_VIEW = None

# Early close when all eligible members have voted
EARLY_CLOSE = True

# Contest agent settings
AGENT_ENABLED = True
AGENT_CHATTINESS = "hype"  # This is a big one — use hype!

# Discord channel ID for contest announcements.
# Leave as None to use the default announcement channel.
# Set to a specific channel ID string if you want a dedicated channel.
DISCORD_CHANNEL_ID = None

# Google Drive file IDs for each character image.
# Get these from the "Anyone with the link" share URL:
#   https://drive.google.com/file/d/{FILE_ID}/view
# Leave as empty string "" if the image isn't uploaded yet.
FILE_IDS: dict[str, str] = {
    "Trog":   "",  # ← paste file ID here (e.g. "1abc123XYZ...")
    "Rocket": "",
    "Mito":   "",
    "Shodoom": "",
    "Skate":  "",
    "Hit":    "",
    "Kronas": "",
    "Porax":  "",
    "Meggo":  "",
    "Wyland": "",
}

# Entry order (matches sort_order in campaign)
ENTRIES = [
    "Trog",
    "Rocket",
    "Mito",
    "Shodoom",
    "Skate",
    "Hit",
    "Kronas",
    "Porax",
    "Meggo",
    "Wyland",
]


def _drive_url(file_id: str) -> str | None:
    """Convert a Google Drive file ID to a direct image URL."""
    if not file_id:
        return None
    return f"https://drive.google.com/uc?id={file_id}&export=view"


# ---------------------------------------------------------------------------
# Main setup logic
# ---------------------------------------------------------------------------


async def setup_campaign() -> None:
    from sqlalchemy import select
    from sv_common.db.engine import get_session_factory
    from sv_common.db.models import Campaign, CampaignEntry, GuildMember, GuildRank
    from patt.services import campaign_service

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL is not set. Check your .env file.")
        sys.exit(1)

    factory = get_session_factory(database_url)

    async with factory() as db:
        # ── Find the Guild Leader to use as campaign creator ───────────────────
        result = await db.execute(
            select(GuildMember)
            .join(GuildRank, GuildMember.rank_id == GuildRank.id)
            .where(GuildRank.level == 5)
            .limit(1)
        )
        admin = result.scalar_one_or_none()
        if admin is None:
            logger.error(
                "No Guild Leader (rank level 5) found. "
                "Run the platform first so ranks are seeded."
            )
            sys.exit(1)
        logger.info("Using '%s' as campaign creator", admin.display_name or admin.discord_username)

        # ── Check if campaign already exists ───────────────────────────────────
        result = await db.execute(
            select(Campaign).where(Campaign.title == CAMPAIGN_TITLE)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            logger.info(
                "Campaign already exists (id=%d, status=%s). "
                "Updating entries...",
                existing.id,
                existing.status,
            )
            campaign = existing
        else:
            # ── Create the campaign ───────────────────────────────────────────
            now = datetime.now(timezone.utc)
            # start_at is set to 1 hour from now — Mike activates manually anyway
            start_at = now + timedelta(hours=1)

            campaign = await campaign_service.create_campaign(
                db,
                title=CAMPAIGN_TITLE,
                description=CAMPAIGN_DESCRIPTION,
                min_rank_to_vote=MIN_RANK_TO_VOTE,
                min_rank_to_view=MIN_RANK_TO_VIEW,
                start_at=start_at,
                duration_hours=DURATION_HOURS,
                picks_per_voter=3,
                early_close_if_all_voted=EARLY_CLOSE,
                created_by=admin.id,
                agent_enabled=AGENT_ENABLED,
                agent_chattiness=AGENT_CHATTINESS,
                discord_channel_id=DISCORD_CHANNEL_ID,
            )
            logger.info("Campaign created: id=%d, status=%s", campaign.id, campaign.status)

        # ── Add / update entries ───────────────────────────────────────────────
        if campaign.status != "draft":
            logger.warning(
                "Campaign is '%s' (not draft). Cannot modify entries. "
                "Use the admin UI at /admin/campaigns/%d to make changes.",
                campaign.status,
                campaign.id,
            )
        else:
            # Load existing entries
            result = await db.execute(
                select(CampaignEntry).where(CampaignEntry.campaign_id == campaign.id)
            )
            existing_entries = {e.name: e for e in result.scalars().all()}

            for sort_order, name in enumerate(ENTRIES):
                image_url = _drive_url(FILE_IDS.get(name, ""))
                if name in existing_entries:
                    entry = existing_entries[name]
                    entry.sort_order = sort_order
                    if image_url:
                        entry.image_url = image_url
                    await db.flush()
                    logger.info("  Updated entry: %s (image=%s)", name, "✓" if image_url else "✗ missing")
                else:
                    await campaign_service.add_entry(
                        db,
                        campaign.id,
                        name=name,
                        image_url=image_url,
                        sort_order=sort_order,
                    )
                    logger.info("  Added entry: %s (image=%s)", name, "✓" if image_url else "✗ missing")

        await db.commit()

    # ── Summary ────────────────────────────────────────────────────────────────
    missing = [name for name in ENTRIES if not FILE_IDS.get(name)]
    print()
    print("=" * 60)
    print(f"Campaign: {CAMPAIGN_TITLE}")
    print(f"Status: {campaign.status.upper()}")
    print(f"Admin URL: /admin/campaigns/{campaign.id}")
    print(f"Vote URL:  /vote/{campaign.id}")
    print()
    if missing:
        print(f"⚠  Missing image URLs for: {', '.join(missing)}")
        print("   Update FILE_IDS in this script and re-run, or")
        print(f"   add images manually via /admin/campaigns/{campaign.id}")
    else:
        print("✓  All 10 image URLs are configured")
    print()
    print("Next steps:")
    print(f"  1. Verify images look correct: /vote/{campaign.id}")
    print(f"  2. When ready, activate at: /admin/campaigns/{campaign.id}")
    print("  3. Or activate via the admin panel → Activate button")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(setup_campaign())
