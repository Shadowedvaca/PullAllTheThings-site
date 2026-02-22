"""Integration tests for the full campaign lifecycle.

Requires TEST_DATABASE_URL pointing to a running PostgreSQL instance.
All tests skip gracefully if the database is unavailable.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import (
    Campaign,
    CampaignEntry,
    GuildMember,
    GuildRank,
)
from patt.services import campaign_service, vote_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_rank(db: AsyncSession, *, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _create_member(
    db: AsyncSession,
    *,
    discord_username: str,
    rank_id: int,
    discord_id: str,
) -> GuildMember:
    member = GuildMember(
        discord_username=discord_username,
        display_name=discord_username,
        discord_id=discord_id,
        rank_id=rank_id,
    )
    db.add(member)
    await db.flush()
    return member


async def _make_live_campaign(
    db: AsyncSession,
    *,
    created_by: int,
    min_rank_to_vote: int = 2,
    min_rank_to_view: int | None = None,
    picks_per_voter: int = 3,
    early_close_if_all_voted: bool = True,
) -> Campaign:
    """Create a live campaign with 3 entries."""
    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db,
        title="Art Vote 2025",
        description="Vote for the guild avatar art",
        min_rank_to_vote=min_rank_to_vote,
        min_rank_to_view=min_rank_to_view,
        start_at=now - timedelta(minutes=1),
        duration_hours=168,
        picks_per_voter=picks_per_voter,
        early_close_if_all_voted=early_close_if_all_voted,
        created_by=created_by,
    )
    # Add entries while draft
    for i in range(1, 4):
        await campaign_service.add_entry(db, campaign.id, name=f"Entry {i}")
    # Activate
    campaign = await campaign_service.activate_campaign(db, campaign.id)
    return campaign


def _auth_headers(member: GuildMember) -> dict:
    """Generate auth headers for a member."""
    from sv_common.auth.jwt import create_access_token

    rank_level = member.rank.level if member.rank else 0
    token = create_access_token(
        user_id=0, member_id=member.id, rank_level=rank_level
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Full lifecycle test
# ---------------------------------------------------------------------------


async def test_full_campaign_lifecycle(db_session: AsyncSession):
    """Create → add entries → activate → vote → close → results."""
    # Setup: officer and two voters
    officer_rank = await _create_rank(db_session, name="Officer", level=4)
    member_rank = await _create_rank(db_session, name="Member", level=2)

    officer = await _create_member(
        db_session,
        discord_username="officer1",
        rank_id=officer_rank.id,
        discord_id="100000000000000001",
    )
    voter1 = await _create_member(
        db_session,
        discord_username="voter1",
        rank_id=member_rank.id,
        discord_id="100000000000000002",
    )
    voter2 = await _create_member(
        db_session,
        discord_username="voter2",
        rank_id=member_rank.id,
        discord_id="100000000000000003",
    )

    # Create campaign (draft)
    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Art Vote",
        min_rank_to_vote=2,
        start_at=now + timedelta(hours=1),
        duration_hours=48,
        created_by=officer.id,
    )
    assert campaign.status == "draft"
    assert campaign.title == "Art Vote"

    # Add entries while draft
    e1 = await campaign_service.add_entry(db_session, campaign.id, name="Dragon")
    e2 = await campaign_service.add_entry(db_session, campaign.id, name="Phoenix")
    e3 = await campaign_service.add_entry(db_session, campaign.id, name="Gryphon")

    # Activate
    campaign = await campaign_service.activate_campaign(db_session, campaign.id)
    assert campaign.status == "live"

    # Cast votes
    picks1 = [
        {"entry_id": e1.id, "rank": 1},
        {"entry_id": e2.id, "rank": 2},
        {"entry_id": e3.id, "rank": 3},
    ]
    votes1 = await vote_service.cast_vote(
        db_session, campaign_id=campaign.id, member_id=voter1.id, picks=picks1
    )
    assert len(votes1) == 3

    picks2 = [
        {"entry_id": e1.id, "rank": 1},
        {"entry_id": e3.id, "rank": 2},
        {"entry_id": e2.id, "rank": 3},
    ]
    await vote_service.cast_vote(
        db_session, campaign_id=campaign.id, member_id=voter2.id, picks=picks2
    )

    # Close campaign
    campaign = await campaign_service.close_campaign(db_session, campaign.id)
    assert campaign.status == "closed"

    # Check results
    results = await vote_service.get_results(db_session, campaign.id)
    assert len(results) == 3
    assert results[0]["entry"]["name"] == "Dragon"  # 2 firsts = 6 pts
    assert results[0]["weighted_score"] == 6
    assert results[0]["final_rank"] == 1


# ---------------------------------------------------------------------------
# Vote validation tests
# ---------------------------------------------------------------------------


async def test_cast_vote_validates_rank_requirement(db_session: AsyncSession):
    """Member with rank below min_rank_to_vote is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer2", level=4)
    low_rank = await _create_rank(db_session, name="Initiate2", level=1)

    officer = await _create_member(
        db_session,
        discord_username="off2",
        rank_id=officer_rank.id,
        discord_id="200000000000000001",
    )
    initiate = await _create_member(
        db_session,
        discord_username="init2",
        rank_id=low_rank.id,
        discord_id="200000000000000002",
    )

    campaign = await _make_live_campaign(
        db_session, created_by=officer.id, min_rank_to_vote=3
    )
    entries = campaign.entries

    with pytest.raises(ValueError, match="rank"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            member_id=initiate.id,
            picks=[
                {"entry_id": entries[0].id, "rank": 1},
                {"entry_id": entries[1].id, "rank": 2},
                {"entry_id": entries[2].id, "rank": 3},
            ],
        )


async def test_cast_vote_rejects_duplicate(db_session: AsyncSession):
    """Voting twice in the same campaign is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer3", level=4)
    member_rank = await _create_rank(db_session, name="Member3", level=2)

    officer = await _create_member(
        db_session, discord_username="off3", rank_id=officer_rank.id, discord_id="300000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter3", rank_id=member_rank.id, discord_id="300000000000000002"
    )

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries
    picks = [
        {"entry_id": entries[0].id, "rank": 1},
        {"entry_id": entries[1].id, "rank": 2},
        {"entry_id": entries[2].id, "rank": 3},
    ]

    await vote_service.cast_vote(db_session, campaign_id=campaign.id, member_id=voter.id, picks=picks)

    with pytest.raises(ValueError, match="already voted"):
        await vote_service.cast_vote(db_session, campaign_id=campaign.id, member_id=voter.id, picks=picks)


async def test_cast_vote_rejects_wrong_number_of_picks(db_session: AsyncSession):
    """Wrong number of picks is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer4", level=4)
    member_rank = await _create_rank(db_session, name="Member4", level=2)

    officer = await _create_member(
        db_session, discord_username="off4", rank_id=officer_rank.id, discord_id="400000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter4", rank_id=member_rank.id, discord_id="400000000000000002"
    )

    campaign = await _make_live_campaign(db_session, created_by=officer.id, picks_per_voter=3)
    entries = campaign.entries

    with pytest.raises(ValueError, match="picks"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            member_id=voter.id,
            picks=[{"entry_id": entries[0].id, "rank": 1}],  # Only 1 pick, need 3
        )


async def test_cast_vote_rejects_duplicate_entries(db_session: AsyncSession):
    """Picks with duplicate entry IDs are rejected."""
    officer_rank = await _create_rank(db_session, name="Officer5", level=4)
    member_rank = await _create_rank(db_session, name="Member5", level=2)

    officer = await _create_member(
        db_session, discord_username="off5", rank_id=officer_rank.id, discord_id="500000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter5", rank_id=member_rank.id, discord_id="500000000000000002"
    )

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    with pytest.raises(ValueError, match="Duplicate entries"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            member_id=voter.id,
            picks=[
                {"entry_id": entries[0].id, "rank": 1},
                {"entry_id": entries[0].id, "rank": 2},  # Duplicate!
                {"entry_id": entries[2].id, "rank": 3},
            ],
        )


async def test_cast_vote_rejects_entry_from_wrong_campaign(db_session: AsyncSession):
    """Entry from another campaign is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer6", level=4)
    member_rank = await _create_rank(db_session, name="Member6", level=2)

    officer = await _create_member(
        db_session, discord_username="off6", rank_id=officer_rank.id, discord_id="600000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter6", rank_id=member_rank.id, discord_id="600000000000000002"
    )

    campaign_a = await _make_live_campaign(db_session, created_by=officer.id)
    entries_a = campaign_a.entries

    # Create a second campaign and get its entries
    campaign_b = await _make_live_campaign(db_session, created_by=officer.id)
    entries_b = campaign_b.entries

    with pytest.raises(ValueError, match="does not belong"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign_a.id,
            member_id=voter.id,
            picks=[
                {"entry_id": entries_a[0].id, "rank": 1},
                {"entry_id": entries_a[1].id, "rank": 2},
                {"entry_id": entries_b[0].id, "rank": 3},  # From wrong campaign!
            ],
        )


# ---------------------------------------------------------------------------
# Results visibility
# ---------------------------------------------------------------------------


async def test_results_hidden_until_voted(db_session: AsyncSession, client: AsyncClient):
    """A member who hasn't voted cannot see live results via the API."""
    officer_rank = await _create_rank(db_session, name="Officer7", level=4)
    member_rank = await _create_rank(db_session, name="Member7", level=2)

    # Eagerly load rank for JWT generation
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_result = await db_session.execute(
        select(GuildRank).where(GuildRank.id == officer_rank.id)
    )
    officer_rank = officer_result.scalar_one()

    officer = await _create_member(
        db_session, discord_username="off7", rank_id=officer_rank.id, discord_id="700000000000000001"
    )
    non_voter = await _create_member(
        db_session, discord_username="nonvoter7", rank_id=member_rank.id, discord_id="700000000000000002"
    )

    # Load members with ranks
    result = await db_session.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .where(GuildMember.id.in_([officer.id, non_voter.id]))
    )
    members = {m.id: m for m in result.scalars().all()}
    officer = members[officer.id]
    non_voter = members[non_voter.id]

    campaign = await _make_live_campaign(db_session, created_by=officer.id)

    response = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(non_voter),
    )
    assert response.status_code == 403


async def test_results_visible_after_voting(db_session: AsyncSession, client: AsyncClient):
    """A member who has voted can see live standings."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer8", level=4)
    member_rank = await _create_rank(db_session, name="Member8", level=2)

    officer = await _create_member(
        db_session, discord_username="off8", rank_id=officer_rank.id, discord_id="800000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter8", rank_id=member_rank.id, discord_id="800000000000000002"
    )

    # Load with ranks
    result = await db_session.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .where(GuildMember.id.in_([voter.id]))
    )
    voter = result.scalar_one()

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    # Vote first
    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        member_id=voter.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},
            {"entry_id": entries[1].id, "rank": 2},
            {"entry_id": entries[2].id, "rank": 3},
        ],
    )

    response = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(voter),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["data"]) == 3


async def test_public_campaign_results_visible_to_anonymous(db_session: AsyncSession, client: AsyncClient):
    """Closed public campaigns show results to anyone (no auth required)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer9", level=4)
    member_rank = await _create_rank(db_session, name="Member9", level=2)

    officer = await _create_member(
        db_session, discord_username="off9", rank_id=officer_rank.id, discord_id="900000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter9", rank_id=member_rank.id, discord_id="900000000000000002"
    )

    # Public campaign (no min_rank_to_view)
    campaign = await _make_live_campaign(
        db_session, created_by=officer.id, min_rank_to_view=None
    )
    entries = campaign.entries

    # Vote then close
    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        member_id=voter.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},
            {"entry_id": entries[1].id, "rank": 2},
            {"entry_id": entries[2].id, "rank": 3},
        ],
    )
    await campaign_service.close_campaign(db_session, campaign.id)

    # Anonymous request — no auth header
    response = await client.get(f"/api/v1/campaigns/{campaign.id}/results")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


async def test_rank_gated_campaign_hidden_from_low_rank(db_session: AsyncSession, client: AsyncClient):
    """Campaign with min_rank_to_view is hidden from low-rank members."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer10", level=4)
    low_rank = await _create_rank(db_session, name="Initiate10", level=1)

    officer = await _create_member(
        db_session, discord_username="off10", rank_id=officer_rank.id, discord_id="1000000000000000001"
    )
    initiate = await _create_member(
        db_session, discord_username="init10", rank_id=low_rank.id, discord_id="1000000000000000002"
    )

    result = await db_session.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .where(GuildMember.id == initiate.id)
    )
    initiate = result.scalar_one()

    # Campaign requires rank 3 to view
    campaign = await _make_live_campaign(
        db_session, created_by=officer.id, min_rank_to_view=3
    )

    response = await client.get(
        f"/api/v1/campaigns/{campaign.id}",
        headers=_auth_headers(initiate),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Early close
# ---------------------------------------------------------------------------


async def test_early_close_when_all_eligible_voted(db_session: AsyncSession):
    """Campaign closes early once all eligible members have voted."""
    officer_rank = await _create_rank(db_session, name="Officer11", level=4)
    member_rank = await _create_rank(db_session, name="Member11", level=2)

    officer = await _create_member(
        db_session, discord_username="off11", rank_id=officer_rank.id, discord_id="1100000000000000001"
    )
    # Only one eligible voter
    voter = await _create_member(
        db_session, discord_username="voter11", rank_id=member_rank.id, discord_id="1100000000000000002"
    )

    # Both officer (level 4) and voter (level 2) are eligible for min_rank_to_vote=2
    campaign = await _make_live_campaign(
        db_session,
        created_by=officer.id,
        min_rank_to_vote=4,  # Only officers can vote — just 1 eligible
        early_close_if_all_voted=True,
    )
    entries = campaign.entries

    # Officer votes — they're the only eligible voter
    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        member_id=officer.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},
            {"entry_id": entries[1].id, "rank": 2},
            {"entry_id": entries[2].id, "rank": 3},
        ],
    )

    # check_early_close should close the campaign
    closed = await vote_service.check_early_close(db_session, campaign.id)
    assert closed is True

    from sqlalchemy import select
    result = await db_session.execute(
        select(Campaign).where(Campaign.id == campaign.id)
    )
    campaign = result.scalar_one()
    assert campaign.status == "closed"


# ---------------------------------------------------------------------------
# Time-based transitions (unit-level, using campaign_service directly)
# ---------------------------------------------------------------------------


async def test_time_based_activation(db_session: AsyncSession):
    """Draft campaign with past start_at activates when checked."""
    officer_rank = await _create_rank(db_session, name="Officer12", level=4)
    officer = await _create_member(
        db_session, discord_username="off12", rank_id=officer_rank.id, discord_id="1200000000000000001"
    )

    # Create campaign with start_at in the past
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Past Start Campaign",
        min_rank_to_vote=4,
        start_at=past,
        duration_hours=48,
        created_by=officer.id,
    )
    assert campaign.status == "draft"

    # Simulating what the background task does: activate if start_at <= now
    from datetime import datetime as dt
    now = dt.now(timezone.utc)
    assert campaign.start_at <= now  # Confirm it's in the past

    campaign = await campaign_service.activate_campaign(db_session, campaign.id)
    assert campaign.status == "live"


async def test_time_based_close(db_session: AsyncSession):
    """Live campaign can be force-closed; results are calculated."""
    officer_rank = await _create_rank(db_session, name="Officer13", level=4)
    member_rank = await _create_rank(db_session, name="Member13", level=2)

    officer = await _create_member(
        db_session, discord_username="off13", rank_id=officer_rank.id, discord_id="1300000000000000001"
    )
    voter = await _create_member(
        db_session, discord_username="voter13", rank_id=member_rank.id, discord_id="1300000000000000002"
    )

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        member_id=voter.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},
            {"entry_id": entries[1].id, "rank": 2},
            {"entry_id": entries[2].id, "rank": 3},
        ],
    )

    # Force close (simulates background task expiry)
    campaign = await campaign_service.close_campaign(db_session, campaign.id)
    assert campaign.status == "closed"

    # Results should be populated
    results = await vote_service.get_results(db_session, campaign.id)
    assert len(results) == 3
    assert results[0]["final_rank"] == 1
