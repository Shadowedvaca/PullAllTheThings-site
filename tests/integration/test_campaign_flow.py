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
    GuildRank,
    Player,
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


async def _create_player(
    db: AsyncSession,
    *,
    display_name: str,
    rank_id: int,
) -> Player:
    player = Player(
        display_name=display_name,
        guild_rank_id=rank_id,
    )
    db.add(player)
    await db.flush()
    return player


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
    for i in range(1, 4):
        await campaign_service.add_entry(db, campaign.id, name=f"Entry {i}")
    campaign = await campaign_service.activate_campaign(db, campaign.id)
    return campaign


def _auth_headers(player: Player) -> dict:
    """Generate auth headers for a player.

    Uses member_id=player.id in JWT; campaign routes fall back to
    member_id lookup when user_id resolves to no player.
    """
    from sv_common.auth.jwt import create_access_token
    from sqlalchemy.orm import selectinload

    rank_level = player.guild_rank.level if player.guild_rank else 0
    token = create_access_token(
        user_id=0, member_id=player.id, rank_level=rank_level
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Full lifecycle test
# ---------------------------------------------------------------------------


async def test_full_campaign_lifecycle(db_session: AsyncSession):
    """Create → add entries → activate → vote → close → results."""
    officer_rank = await _create_rank(db_session, name="Officer", level=4)
    member_rank = await _create_rank(db_session, name="Member", level=2)

    officer = await _create_player(
        db_session,
        display_name="officer1",
        rank_id=officer_rank.id,
    )
    voter1 = await _create_player(
        db_session,
        display_name="voter1",
        rank_id=member_rank.id,
    )
    voter2 = await _create_player(
        db_session,
        display_name="voter2",
        rank_id=member_rank.id,
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
        db_session, campaign_id=campaign.id, player_id=voter1.id, picks=picks1
    )
    assert len(votes1) == 3

    picks2 = [
        {"entry_id": e1.id, "rank": 1},
        {"entry_id": e3.id, "rank": 2},
        {"entry_id": e2.id, "rank": 3},
    ]
    await vote_service.cast_vote(
        db_session, campaign_id=campaign.id, player_id=voter2.id, picks=picks2
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
    """Player with rank below min_rank_to_vote is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer2", level=4)
    low_rank = await _create_rank(db_session, name="Initiate2", level=1)

    officer = await _create_player(
        db_session,
        display_name="off2",
        rank_id=officer_rank.id,
    )
    initiate = await _create_player(
        db_session,
        display_name="init2",
        rank_id=low_rank.id,
    )

    campaign = await _make_live_campaign(
        db_session, created_by=officer.id, min_rank_to_vote=3
    )
    entries = campaign.entries

    with pytest.raises(ValueError, match="rank"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            player_id=initiate.id,
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

    officer = await _create_player(db_session, display_name="off3", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter3", rank_id=member_rank.id)

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries
    picks = [
        {"entry_id": entries[0].id, "rank": 1},
        {"entry_id": entries[1].id, "rank": 2},
        {"entry_id": entries[2].id, "rank": 3},
    ]

    await vote_service.cast_vote(db_session, campaign_id=campaign.id, player_id=voter.id, picks=picks)

    with pytest.raises(ValueError, match="already voted"):
        await vote_service.cast_vote(db_session, campaign_id=campaign.id, player_id=voter.id, picks=picks)


async def test_cast_vote_rejects_wrong_number_of_picks(db_session: AsyncSession):
    """Wrong number of picks is rejected."""
    officer_rank = await _create_rank(db_session, name="Officer4", level=4)
    member_rank = await _create_rank(db_session, name="Member4", level=2)

    officer = await _create_player(db_session, display_name="off4", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter4", rank_id=member_rank.id)

    campaign = await _make_live_campaign(db_session, created_by=officer.id, picks_per_voter=3)
    entries = campaign.entries

    with pytest.raises(ValueError, match="picks"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            player_id=voter.id,
            picks=[{"entry_id": entries[0].id, "rank": 1}],  # Only 1 pick, need 3
        )


async def test_cast_vote_rejects_duplicate_entries(db_session: AsyncSession):
    """Picks with duplicate entry IDs are rejected."""
    officer_rank = await _create_rank(db_session, name="Officer5", level=4)
    member_rank = await _create_rank(db_session, name="Member5", level=2)

    officer = await _create_player(db_session, display_name="off5", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter5", rank_id=member_rank.id)

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    with pytest.raises(ValueError, match="Duplicate entries"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            player_id=voter.id,
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

    officer = await _create_player(db_session, display_name="off6", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter6", rank_id=member_rank.id)

    campaign_a = await _make_live_campaign(db_session, created_by=officer.id)
    entries_a = campaign_a.entries

    campaign_b = await _make_live_campaign(db_session, created_by=officer.id)
    entries_b = campaign_b.entries

    with pytest.raises(ValueError, match="does not belong"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign_a.id,
            player_id=voter.id,
            picks=[
                {"entry_id": entries_a[0].id, "rank": 1},
                {"entry_id": entries_a[1].id, "rank": 2},
                {"entry_id": entries_b[0].id, "rank": 3},  # From wrong campaign!
            ],
        )


# ---------------------------------------------------------------------------
# Results visibility (HTTP tests)
# ---------------------------------------------------------------------------


async def test_results_hidden_until_voted(db_session: AsyncSession, client: AsyncClient):
    """A player who hasn't voted cannot see live results via the API."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer7", level=4)
    member_rank = await _create_rank(db_session, name="Member7", level=2)

    officer = await _create_player(db_session, display_name="off7", rank_id=officer_rank.id)
    non_voter = await _create_player(db_session, display_name="nonvoter7", rank_id=member_rank.id)

    # Eagerly load ranks for auth header generation
    result = await db_session.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.id.in_([officer.id, non_voter.id]))
    )
    players = {p.id: p for p in result.scalars().all()}
    non_voter = players[non_voter.id]

    campaign = await _make_live_campaign(db_session, created_by=officer.id)

    response = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(non_voter),
    )
    assert response.status_code == 403


async def test_results_visible_after_voting(db_session: AsyncSession, client: AsyncClient):
    """A player who has voted can see live standings."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer8", level=4)
    member_rank = await _create_rank(db_session, name="Member8", level=2)

    officer = await _create_player(db_session, display_name="off8", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter8", rank_id=member_rank.id)

    result = await db_session.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.id == voter.id)
    )
    voter = result.scalar_one()

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        player_id=voter.id,
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
    officer_rank = await _create_rank(db_session, name="Officer9", level=4)
    member_rank = await _create_rank(db_session, name="Member9", level=2)

    officer = await _create_player(db_session, display_name="off9", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter9", rank_id=member_rank.id)

    campaign = await _make_live_campaign(
        db_session, created_by=officer.id, min_rank_to_view=None
    )
    entries = campaign.entries

    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        player_id=voter.id,
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
    """Campaign with min_rank_to_view is hidden from low-rank players."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    officer_rank = await _create_rank(db_session, name="Officer10", level=4)
    low_rank = await _create_rank(db_session, name="Initiate10", level=1)

    officer = await _create_player(db_session, display_name="off10", rank_id=officer_rank.id)
    initiate = await _create_player(db_session, display_name="init10", rank_id=low_rank.id)

    result = await db_session.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.id == initiate.id)
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
    """Campaign closes early once all eligible players have voted."""
    officer_rank = await _create_rank(db_session, name="Officer11", level=4)
    member_rank = await _create_rank(db_session, name="Member11", level=2)

    officer = await _create_player(db_session, display_name="off11", rank_id=officer_rank.id)
    await _create_player(db_session, display_name="voter11", rank_id=member_rank.id)

    # Both officer (level 4) and voter (level 2) are eligible for min_rank_to_vote=2
    # But we use min_rank_to_vote=4 so only the officer can vote
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
        player_id=officer.id,
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
# Time-based transitions
# ---------------------------------------------------------------------------


async def test_time_based_activation(db_session: AsyncSession):
    """Draft campaign with past start_at activates when checked."""
    officer_rank = await _create_rank(db_session, name="Officer12", level=4)
    officer = await _create_player(db_session, display_name="off12", rank_id=officer_rank.id)

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

    now = datetime.now(timezone.utc)
    assert campaign.start_at <= now

    campaign = await campaign_service.activate_campaign(db_session, campaign.id)
    assert campaign.status == "live"


async def test_time_based_close(db_session: AsyncSession):
    """Live campaign can be force-closed; results are calculated."""
    officer_rank = await _create_rank(db_session, name="Officer13", level=4)
    member_rank = await _create_rank(db_session, name="Member13", level=2)

    officer = await _create_player(db_session, display_name="off13", rank_id=officer_rank.id)
    voter = await _create_player(db_session, display_name="voter13", rank_id=member_rank.id)

    campaign = await _make_live_campaign(db_session, created_by=officer.id)
    entries = campaign.entries

    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        player_id=voter.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},
            {"entry_id": entries[1].id, "rank": 2},
            {"entry_id": entries[2].id, "rank": 3},
        ],
    )

    campaign = await campaign_service.close_campaign(db_session, campaign.id)
    assert campaign.status == "closed"

    results = await vote_service.get_results(db_session, campaign.id)
    assert len(results) == 3
    assert results[0]["final_rank"] == 1
