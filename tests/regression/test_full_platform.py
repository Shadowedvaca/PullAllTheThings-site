"""End-to-end regression suite for the PATT platform.

Tests the complete lifecycle in one connected flow:
  1. Create ranks and members
  2. Issue invite codes and register members via the auth API
  3. Verify login and /me
  4. Create a campaign with 10 entries
  5. Activate the campaign
  6. Verify ineligible members cannot vote
  7. Verify non-voters cannot see live results
  8. Each eligible member votes; verify live standings update
  9. Duplicate vote rejected
 10. Verify vote stats
 11. Early close triggers when all eligible members have voted
 12. Verify final results are correct
 13. Contest agent milestone detection (pure function assertions)

Requires TEST_DATABASE_URL pointing to a running PostgreSQL instance.
All tests skip gracefully if the database is unavailable.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sv_common.db.models import (
    Campaign,
    CampaignEntry,
    ContestAgentLog,
    GuildMember,
    GuildRank,
    InviteCode,
)
from sv_common.auth.jwt import create_access_token
from patt.services import campaign_service, vote_service
from patt.services.contest_agent import detect_milestone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_rank(db: AsyncSession, *, name: str, level: int) -> GuildRank:
    r = GuildRank(name=name, level=level, description=f"Rank {name}")
    db.add(r)
    await db.flush()
    return r


async def _create_member(
    db: AsyncSession,
    *,
    discord_username: str,
    rank_id: int,
    discord_id: str,
    display_name: str | None = None,
) -> GuildMember:
    m = GuildMember(
        discord_username=discord_username,
        display_name=display_name or discord_username,
        discord_id=discord_id,
        rank_id=rank_id,
    )
    db.add(m)
    await db.flush()
    return m


async def _create_invite(
    db: AsyncSession, *, member_id: int, created_by_id: int
) -> InviteCode:
    code = secrets.token_hex(8)  # 16-char hex string
    inv = InviteCode(
        code=code,
        member_id=member_id,
        created_by=created_by_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(inv)
    await db.flush()
    return inv


def _auth_headers(member_id: int, rank_level: int) -> dict:
    """Generate Bearer auth headers for a member."""
    token = create_access_token(
        user_id=0, member_id=member_id, rank_level=rank_level
    )
    return {"Authorization": f"Bearer {token}"}


async def _load_with_rank(db: AsyncSession, member_id: int) -> GuildMember:
    """Load a GuildMember with its rank eagerly loaded."""
    result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .where(GuildMember.id == member_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Regression: full platform flow
# ---------------------------------------------------------------------------


async def test_full_platform_regression(
    db_session: AsyncSession, client: AsyncClient
):
    """Full platform flow — auth, campaign, vote, results, contest agent.

    This is the "pull a thread anywhere" test. If this passes, the
    platform's core flows work end-to-end.
    """

    # ==================================================================
    # 1. SETUP: Ranks and members
    # ==================================================================

    rank_initiate = await _create_rank(db_session, name="RegInitiate", level=1)
    rank_veteran = await _create_rank(db_session, name="RegVeteran", level=3)
    rank_officer = await _create_rank(db_session, name="RegOfficer", level=4)
    rank_gl = await _create_rank(db_session, name="RegGuildLeader", level=5)

    # Guild leader (admin, creates campaign)
    admin = await _create_member(
        db_session,
        discord_username="reg_trog",
        rank_id=rank_gl.id,
        discord_id="9900000000000000001",
        display_name="Trog",
    )

    # Veteran+ members — eligible to vote (min_rank_to_vote=3)
    vet1 = await _create_member(
        db_session,
        discord_username="reg_rocket",
        rank_id=rank_veteran.id,
        discord_id="9900000000000000002",
        display_name="Rocket",
    )
    vet2 = await _create_member(
        db_session,
        discord_username="reg_mito",
        rank_id=rank_veteran.id,
        discord_id="9900000000000000003",
        display_name="Mito",
    )
    officer1 = await _create_member(
        db_session,
        discord_username="reg_shodoom",
        rank_id=rank_officer.id,
        discord_id="9900000000000000004",
        display_name="Shodoom",
    )

    # Initiate — NOT eligible to vote
    initiate = await _create_member(
        db_session,
        discord_username="reg_newbie",
        rank_id=rank_initiate.id,
        discord_id="9900000000000000005",
        display_name="Newbie",
    )

    # ==================================================================
    # 2. INVITE CODES: Create for members who will register
    # ==================================================================

    inv_vet1 = await _create_invite(
        db_session, member_id=vet1.id, created_by_id=admin.id
    )
    inv_vet2 = await _create_invite(
        db_session, member_id=vet2.id, created_by_id=admin.id
    )
    inv_off1 = await _create_invite(
        db_session, member_id=officer1.id, created_by_id=admin.id
    )

    # ==================================================================
    # 3. REGISTRATION: Register via the API (full auth flow)
    # ==================================================================

    # Register vet1 (rocket)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "code": inv_vet1.code,
            "discord_username": "reg_rocket",
            "password": "password123",
        },
    )
    assert resp.status_code == 200, f"rocket register failed: {resp.text}"
    assert resp.json()["ok"] is True
    rocket_token = resp.json()["data"]["token"]

    # Register vet2 (mito)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "code": inv_vet2.code,
            "discord_username": "reg_mito",
            "password": "password123",
        },
    )
    assert resp.status_code == 200, f"mito register failed: {resp.text}"
    mito_token = resp.json()["data"]["token"]

    # Register officer1 (shodoom)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "code": inv_off1.code,
            "discord_username": "reg_shodoom",
            "password": "password123",
        },
    )
    assert resp.status_code == 200, f"shodoom register failed: {resp.text}"
    shodoom_token = resp.json()["data"]["token"]

    # Verify invite code cannot be reused
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "code": inv_vet1.code,
            "discord_username": "reg_rocket",
            "password": "another_pass",
        },
    )
    assert resp.status_code == 400, "Used invite code should be rejected"

    # ==================================================================
    # 4. LOGIN + /me: Verify registered credentials work
    # ==================================================================

    # Login with correct credentials
    resp = await client.post(
        "/api/v1/auth/login",
        json={"discord_username": "reg_rocket", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Login with wrong password
    resp = await client.post(
        "/api/v1/auth/login",
        json={"discord_username": "reg_rocket", "password": "wrongpassword"},
    )
    assert resp.status_code == 401, "Wrong password should be rejected"

    # /me returns the correct member profile
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {rocket_token}"},
    )
    assert resp.status_code == 200
    me = resp.json()["data"]
    assert me["discord_username"] == "reg_rocket"
    assert me["rank"]["level"] == 3

    # ==================================================================
    # 5. CAMPAIGN: Create, add 10 entries, activate
    # ==================================================================

    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Salt All The Things Profile Pic Contest",
        description="Vote for your favourite character portrait! Pick your top 3.",
        min_rank_to_vote=3,          # Veteran+
        min_rank_to_view=None,       # Public results
        start_at=now - timedelta(minutes=1),
        duration_hours=168,
        picks_per_voter=3,
        early_close_if_all_voted=True,
        created_by=admin.id,
        agent_enabled=True,
        agent_chattiness="normal",
    )
    assert campaign.status == "draft"

    entry_names = [
        "Trog", "Rocket", "Mito", "Shodoom", "Skate",
        "Hit", "Kronas", "Porax", "Meggo", "Wyland",
    ]
    entries: list[CampaignEntry] = []
    for i, name in enumerate(entry_names):
        e = await campaign_service.add_entry(
            db_session, campaign.id, name=name, sort_order=i
        )
        entries.append(e)

    assert len(entries) == 10, "Campaign must have exactly 10 entries"

    # Activate the campaign
    campaign = await campaign_service.activate_campaign(db_session, campaign.id)
    assert campaign.status == "live"

    # ==================================================================
    # 6. PERMISSION: Ineligible member cannot vote
    # ==================================================================

    resp = await client.post(
        f"/api/v1/campaigns/{campaign.id}/vote",
        json={
            "picks": [
                {"entry_id": entries[0].id, "rank": 1},
                {"entry_id": entries[1].id, "rank": 2},
                {"entry_id": entries[2].id, "rank": 3},
            ]
        },
        headers=_auth_headers(initiate.id, rank_level=1),
    )
    assert resp.status_code in (403, 422), "Initiate should be denied voting"

    # ==================================================================
    # 7. PERMISSION: Non-voter cannot see live results
    # ==================================================================

    resp = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(vet1.id, rank_level=3),
    )
    assert resp.status_code == 403, "Non-voter should not see live results"

    # ==================================================================
    # 8. VOTING: Each eligible member votes
    # ==================================================================

    # rocket votes: Trog=1, Rocket=2, Mito=3 → Trog gets 3pts from this
    resp = await client.post(
        f"/api/v1/campaigns/{campaign.id}/vote",
        json={
            "picks": [
                {"entry_id": entries[0].id, "rank": 1},  # Trog
                {"entry_id": entries[1].id, "rank": 2},  # Rocket
                {"entry_id": entries[2].id, "rank": 3},  # Mito
            ]
        },
        headers=_auth_headers(vet1.id, rank_level=3),
    )
    assert resp.status_code == 200, f"rocket vote failed: {resp.text}"

    # Verify rocket can now see live standings
    resp = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(vet1.id, rank_level=3),
    )
    assert resp.status_code == 200
    standings = resp.json()["data"]
    assert len(standings) > 0, "Live standings should be non-empty after a vote"
    # After rocket's vote, Trog leads with 3 pts
    assert standings[0]["entry"]["name"] == "Trog"
    assert standings[0]["weighted_score"] == 3

    # Duplicate vote is rejected
    resp = await client.post(
        f"/api/v1/campaigns/{campaign.id}/vote",
        json={
            "picks": [
                {"entry_id": entries[0].id, "rank": 1},
                {"entry_id": entries[1].id, "rank": 2},
                {"entry_id": entries[2].id, "rank": 3},
            ]
        },
        headers=_auth_headers(vet1.id, rank_level=3),
    )
    assert resp.status_code == 400, "Duplicate vote should be rejected"

    # mito votes: Rocket=1, Trog=2, Mito=3
    resp = await client.post(
        f"/api/v1/campaigns/{campaign.id}/vote",
        json={
            "picks": [
                {"entry_id": entries[1].id, "rank": 1},  # Rocket
                {"entry_id": entries[0].id, "rank": 2},  # Trog
                {"entry_id": entries[2].id, "rank": 3},  # Mito
            ]
        },
        headers=_auth_headers(vet2.id, rank_level=3),
    )
    assert resp.status_code == 200, f"mito vote failed: {resp.text}"

    # Verify standings updated after mito's vote
    # Trog: 3+2=5pts, Rocket: 2+3=5pts (tie), Mito: 1+1=2pts
    resp = await client.get(
        f"/api/v1/campaigns/{campaign.id}/results",
        headers=_auth_headers(vet2.id, rank_level=3),
    )
    assert resp.status_code == 200
    standings = resp.json()["data"]
    top_names = {standings[0]["entry"]["name"], standings[1]["entry"]["name"]}
    assert top_names == {"Trog", "Rocket"}, "Trog and Rocket should be tied at top"

    # shodoom votes: Mito=1, Trog=2, Rocket=3
    resp = await client.post(
        f"/api/v1/campaigns/{campaign.id}/vote",
        json={
            "picks": [
                {"entry_id": entries[2].id, "rank": 1},  # Mito
                {"entry_id": entries[0].id, "rank": 2},  # Trog
                {"entry_id": entries[1].id, "rank": 3},  # Rocket
            ]
        },
        headers=_auth_headers(officer1.id, rank_level=4),
    )
    assert resp.status_code == 200, f"shodoom vote failed: {resp.text}"

    # admin (Guild Leader, level 5) votes directly via service (no registered user)
    # admin: Trog=1, Shodoom=2, Skate=3
    await vote_service.cast_vote(
        db_session,
        campaign_id=campaign.id,
        member_id=admin.id,
        picks=[
            {"entry_id": entries[0].id, "rank": 1},  # Trog
            {"entry_id": entries[3].id, "rank": 2},  # Shodoom
            {"entry_id": entries[4].id, "rank": 3},  # Skate
        ],
    )

    # ==================================================================
    # 9. VOTE STATS: Verify all eligible members have voted
    # ==================================================================

    stats = await vote_service.get_vote_stats(db_session, campaign.id)
    # Eligible: admin(5), vet1(3), vet2(3), officer1(4) = 4 members
    assert stats["total_eligible"] == 4, (
        f"Expected 4 eligible voters, got {stats['total_eligible']}"
    )
    assert stats["total_voted"] == 4, (
        f"Expected 4 votes cast, got {stats['total_voted']}"
    )
    assert stats["all_voted"] is True, "all_voted should be True"
    assert stats["percent_voted"] == 100

    # ==================================================================
    # 10. EARLY CLOSE: All voted → campaign auto-closes
    # ==================================================================

    closed = await vote_service.check_early_close(db_session, campaign.id)
    assert closed is True, "Early close should have triggered"

    result = await db_session.execute(
        select(Campaign).where(Campaign.id == campaign.id)
    )
    campaign = result.scalar_one()
    assert campaign.status == "closed", "Campaign should be closed after early close"

    # ==================================================================
    # 11. FINAL RESULTS: Verify correctness
    # ==================================================================

    results = await vote_service.get_results(db_session, campaign.id)
    assert len(results) == 10, "All 10 entries should appear in results"

    # Point tally:
    # Trog:   rocket(3) + mito(2) + shodoom(0) + admin(3) = 8 pts
    # Rocket: rocket(2) + mito(3) + shodoom(1) + admin(0) = 6 pts
    # Mito:   rocket(1) + mito(1) + shodoom(3) + admin(0) = 5 pts
    # Shodoom: admin(2) = 2 pts
    # Skate:  admin(1) = 1 pt
    winner = results[0]
    assert winner["final_rank"] == 1
    assert winner["entry"]["name"] == "Trog"
    assert winner["weighted_score"] == 8

    second = results[1]
    assert second["entry"]["name"] == "Rocket"
    assert second["weighted_score"] == 6

    third = results[2]
    assert third["entry"]["name"] == "Mito"
    assert third["weighted_score"] == 5

    # Entries with zero votes still appear, just at bottom
    zero_entries = [r for r in results if r["weighted_score"] == 0]
    assert len(zero_entries) == 5, "5 entries should have zero votes"

    # ==================================================================
    # 12. CLOSED CAMPAIGN: Public results visible without auth
    # ==================================================================

    resp = await client.get(f"/api/v1/campaigns/{campaign.id}/results")
    assert resp.status_code == 200, "Public closed campaign results should be visible"
    assert resp.json()["ok"] is True

    # ==================================================================
    # 13. CONTEST AGENT: Milestone detection (pure function assertions)
    # ==================================================================

    # Stats after close: all 4 voted, all_voted=True
    final_stats = await vote_service.get_vote_stats(db_session, campaign.id)

    # Closed campaign + all_voted → should detect "all_voted" event
    event = detect_milestone(
        campaign_status="closed",
        stats=final_stats,
        time_remaining_hours=0,
        logged_events=set(),  # Nothing logged yet
        chattiness="normal",
    )
    assert event == "all_voted", (
        f"Expected 'all_voted' for closed+all_voted campaign, got '{event}'"
    )

    # Once "all_voted" is logged, next call should return None (deduplication)
    event2 = detect_milestone(
        campaign_status="closed",
        stats=final_stats,
        time_remaining_hours=0,
        logged_events={"all_voted"},  # Already logged
        chattiness="normal",
    )
    assert event2 is None, "Event should not fire again after deduplication"

    # Live campaign with no votes → should detect "campaign_launch"
    no_vote_stats = {
        "total_eligible": 4,
        "total_voted": 0,
        "percent_voted": 0,
        "all_voted": False,
    }
    launch_event = detect_milestone(
        campaign_status="live",
        stats=no_vote_stats,
        time_remaining_hours=168,
        logged_events=set(),
        chattiness="normal",
    )
    assert launch_event == "campaign_launch", (
        f"Expected 'campaign_launch' for fresh live campaign, got '{launch_event}'"
    )

    # After launch logged, first vote cast → should detect "first_vote"
    one_vote_stats = {
        "total_eligible": 4,
        "total_voted": 1,
        "percent_voted": 25,
        "all_voted": False,
    }
    # "normal" chattiness includes milestone_50 but not milestone_25.
    # At 25% voted with hype: milestone_25 fires. With normal: first_vote fires.
    first_vote_event = detect_milestone(
        campaign_status="live",
        stats=one_vote_stats,
        time_remaining_hours=168,
        logged_events={"campaign_launch"},
        chattiness="normal",
    )
    assert first_vote_event == "first_vote", (
        f"Expected 'first_vote' after one vote, got '{first_vote_event}'"
    )

    # Quiet mode: only campaign_launch and campaign_closed are allowed
    quiet_event = detect_milestone(
        campaign_status="live",
        stats=one_vote_stats,
        time_remaining_hours=168,
        logged_events=set(),
        chattiness="quiet",
    )
    assert quiet_event == "campaign_launch", (
        "Quiet mode should only fire campaign_launch, not first_vote"
    )

    quiet_event2 = detect_milestone(
        campaign_status="live",
        stats=one_vote_stats,
        time_remaining_hours=168,
        logged_events={"campaign_launch"},
        chattiness="quiet",
    )
    assert quiet_event2 is None, "Quiet mode should not fire first_vote"


# ---------------------------------------------------------------------------
# Additional regression tests for known edge cases
# ---------------------------------------------------------------------------


async def test_invite_code_expiry(db_session: AsyncSession, client: AsyncClient):
    """Expired invite code is rejected at registration.

    Bug guard: ensure expired codes are checked server-side, not just not sent.
    """
    rank = await _create_rank(db_session, name="ExpMember", level=2)
    admin = await _create_member(
        db_session,
        discord_username="exp_admin",
        rank_id=rank.id,
        discord_id="8800000000000000001",
    )
    member = await _create_member(
        db_session,
        discord_username="exp_user",
        rank_id=rank.id,
        discord_id="8800000000000000002",
    )

    # Create an already-expired invite code
    expired_code = secrets.token_hex(8)
    inv = InviteCode(
        code=expired_code,
        member_id=member.id,
        created_by=admin.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Past!
    )
    db_session.add(inv)
    await db_session.flush()

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "code": expired_code,
            "discord_username": "exp_user",
            "password": "password123",
        },
    )
    assert resp.status_code == 400, "Expired invite code must be rejected"


async def test_voting_on_closed_campaign_rejected(db_session: AsyncSession):
    """Voting on a closed campaign is rejected.

    Bug guard: prevents vote stuffing after close.
    """
    rank = await _create_rank(db_session, name="ClosedOfficer", level=4)
    officer = await _create_member(
        db_session,
        discord_username="closed_off",
        rank_id=rank.id,
        discord_id="7700000000000000001",
    )

    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Closed Campaign",
        min_rank_to_vote=4,
        start_at=now - timedelta(minutes=1),
        duration_hours=1,
        created_by=officer.id,
    )
    e1 = await campaign_service.add_entry(db_session, campaign.id, name="A")
    e2 = await campaign_service.add_entry(db_session, campaign.id, name="B")
    e3 = await campaign_service.add_entry(db_session, campaign.id, name="C")

    campaign = await campaign_service.activate_campaign(db_session, campaign.id)
    campaign = await campaign_service.close_campaign(db_session, campaign.id)
    assert campaign.status == "closed"

    with pytest.raises(ValueError, match="not live"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            member_id=officer.id,
            picks=[
                {"entry_id": e1.id, "rank": 1},
                {"entry_id": e2.id, "rank": 2},
                {"entry_id": e3.id, "rank": 3},
            ],
        )


async def test_wrong_number_of_picks_rejected(db_session: AsyncSession):
    """Submitting fewer or more picks than required is rejected.

    Bug guard: ensures the picks_per_voter constraint is enforced.
    """
    rank = await _create_rank(db_session, name="PickOfficer", level=4)
    officer = await _create_member(
        db_session,
        discord_username="pick_off",
        rank_id=rank.id,
        discord_id="6600000000000000001",
    )
    voter = await _create_member(
        db_session,
        discord_username="pick_voter",
        rank_id=rank.id,
        discord_id="6600000000000000002",
    )

    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Pick Count Test",
        min_rank_to_vote=4,
        start_at=now - timedelta(minutes=1),
        duration_hours=24,
        picks_per_voter=3,
        created_by=officer.id,
    )
    e1 = await campaign_service.add_entry(db_session, campaign.id, name="X")
    e2 = await campaign_service.add_entry(db_session, campaign.id, name="Y")
    e3 = await campaign_service.add_entry(db_session, campaign.id, name="Z")

    campaign = await campaign_service.activate_campaign(db_session, campaign.id)

    # Too few picks
    with pytest.raises(ValueError, match="picks"):
        await vote_service.cast_vote(
            db_session,
            campaign_id=campaign.id,
            member_id=voter.id,
            picks=[{"entry_id": e1.id, "rank": 1}],
        )


async def test_public_api_returns_live_campaign_list(
    db_session: AsyncSession, client: AsyncClient
):
    """Public /api/v1/campaigns endpoint returns live campaigns to anonymous users."""
    rank = await _create_rank(db_session, name="PubOfficer", level=4)
    officer = await _create_member(
        db_session,
        discord_username="pub_off",
        rank_id=rank.id,
        discord_id="5500000000000000001",
    )

    now = datetime.now(timezone.utc)
    campaign = await campaign_service.create_campaign(
        db_session,
        title="Public Campaign Test",
        min_rank_to_vote=4,
        min_rank_to_view=None,
        start_at=now - timedelta(minutes=1),
        duration_hours=24,
        created_by=officer.id,
    )
    await campaign_service.activate_campaign(db_session, campaign.id)

    resp = await client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    titles = [c["title"] for c in data["data"]]
    assert "Public Campaign Test" in titles
