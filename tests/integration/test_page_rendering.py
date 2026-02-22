"""Integration tests for Phase 4: page rendering.

Tests that:
- Public pages render correctly (200 status, expected content)
- Auth-gated pages redirect to login when not authenticated
- Auth pages render and function
- Vote page renders for different member states
- Admin pages require Officer+ rank

Requires TEST_DATABASE_URL to be set to a live PostgreSQL test database.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import (
    GuildMember, GuildRank, User, Campaign, CampaignEntry,
    InviteCode,
)
from sv_common.auth.passwords import hash_password
from sv_common.auth.jwt import create_access_token
from patt.deps import COOKIE_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(member: GuildMember, rank_level: int = 0) -> str:
    return create_access_token(
        user_id=member.user_id or 0,
        member_id=member.id,
        rank_level=member.rank.level if member.rank else rank_level,
    )


def _auth_cookies(token: str) -> dict:
    return {COOKIE_NAME: token}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def officer_member_with_user(db_session: AsyncSession):
    """Officer rank member with registered user account."""
    rank = GuildRank(name="Officer_pr", level=4, description="Officer")
    db_session.add(rank)
    await db_session.flush()

    user = User(password_hash=hash_password("testpass123"), is_active=True)
    db_session.add(user)
    await db_session.flush()

    member = GuildMember(
        user_id=user.id,
        discord_username="officer_test",
        display_name="Officer",
        discord_id="444444444444444444",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()

    # reload rank relationship
    member.rank = rank
    return member


@pytest_asyncio.fixture
async def member_with_user(db_session: AsyncSession):
    """Member rank member with registered user account."""
    rank = GuildRank(name="Member_pr", level=2, description="Member")
    db_session.add(rank)
    await db_session.flush()

    user = User(password_hash=hash_password("testpass123"), is_active=True)
    db_session.add(user)
    await db_session.flush()

    member = GuildMember(
        user_id=user.id,
        discord_username="member_test",
        display_name="Member",
        discord_id="555555555555555555",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()

    member.rank = rank
    return member


@pytest_asyncio.fixture
async def live_campaign_with_entries(db_session: AsyncSession, member_with_user):
    """A live campaign with 3 entries, min_rank_to_vote=2."""
    campaign = Campaign(
        title="Test Art Vote",
        description="Pick your favorites",
        type="ranked_choice",
        picks_per_voter=3,
        min_rank_to_vote=2,
        min_rank_to_view=None,
        start_at=datetime.now(timezone.utc) - timedelta(hours=1),
        duration_hours=168,
        status="live",
        early_close_if_all_voted=False,
        created_by=member_with_user.id,
    )
    db_session.add(campaign)
    await db_session.flush()

    for i in range(3):
        entry = CampaignEntry(
            campaign_id=campaign.id,
            name=f"Entry {i + 1}",
            description=f"Test entry {i + 1}",
            sort_order=i,
        )
        db_session.add(entry)
    await db_session.flush()

    # Reload with entries
    from patt.services.campaign_service import get_campaign
    return await get_campaign(db_session, campaign.id)


@pytest_asyncio.fixture
async def invite_code_for_member(db_session: AsyncSession):
    """A fresh member with an unused invite code."""
    rank = GuildRank(name="Initiate_ic", level=1, description="Initiate")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="invite_test_user",
        display_name="Invitee",
        discord_id="666666666666666666",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()

    invite = InviteCode(
        code="TEST-INVITE-CODE",
        member_id=member.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.flush()

    member.rank = rank
    return member, invite


# ---------------------------------------------------------------------------
# Public page tests
# ---------------------------------------------------------------------------


async def test_public_landing_page_renders(client: AsyncClient):
    """GET / → 200, contains guild name."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "Pull All The Things" in response.text


async def test_public_landing_page_shows_login_link_when_anonymous(client: AsyncClient):
    """GET / → contains login link for anonymous visitors."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "/login" in response.text


async def test_public_landing_page_shows_username_when_logged_in(
    client: AsyncClient, member_with_user: GuildMember
):
    """GET / with auth cookie → shows member's display name."""
    token = _make_token(member_with_user)
    response = await client.get("/", cookies=_auth_cookies(token))
    assert response.status_code == 200
    assert member_with_user.display_name in response.text


# ---------------------------------------------------------------------------
# Auth page tests
# ---------------------------------------------------------------------------


async def test_login_page_renders(client: AsyncClient):
    """GET /login → 200, contains login form."""
    response = await client.get("/login")
    assert response.status_code == 200
    assert "discord_username" in response.text
    assert "password" in response.text


async def test_register_page_renders(client: AsyncClient):
    """GET /register → 200, contains registration form."""
    response = await client.get("/register")
    assert response.status_code == 200
    assert "code" in response.text
    assert "discord_username" in response.text


async def test_login_redirects_authenticated_user(
    client: AsyncClient, member_with_user: GuildMember
):
    """GET /login when already logged in → redirects to home."""
    token = _make_token(member_with_user)
    response = await client.get("/login", cookies=_auth_cookies(token), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


async def test_login_post_valid_credentials(
    client: AsyncClient, member_with_user: GuildMember
):
    """POST /login with correct credentials → sets cookie and redirects."""
    response = await client.post(
        "/login",
        data={
            "discord_username": member_with_user.discord_username,
            "password": "testpass123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert COOKIE_NAME in response.cookies


async def test_login_post_invalid_credentials(client: AsyncClient):
    """POST /login with wrong password → 400 with error message."""
    response = await client.post(
        "/login",
        data={"discord_username": "nobody", "password": "wrongpass"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Invalid" in response.text


async def test_logout_clears_cookie(
    client: AsyncClient, member_with_user: GuildMember
):
    """GET /logout → redirects to home, cookie cleared."""
    token = _make_token(member_with_user)
    response = await client.get(
        "/logout",
        cookies=_auth_cookies(token),
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    # Cookie should be cleared (empty value or deleted)
    cookie_val = response.cookies.get(COOKIE_NAME, "")
    assert cookie_val == "" or COOKIE_NAME not in response.cookies


async def test_register_post_mismatched_passwords(client: AsyncClient):
    """POST /register with mismatched passwords → 400 with error."""
    response = await client.post(
        "/register",
        data={
            "code": "SOME-CODE",
            "discord_username": "testuser",
            "password": "password123",
            "password2": "different456",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Passwords do not match" in response.text


async def test_register_post_invalid_invite_code(client: AsyncClient):
    """POST /register with a nonexistent invite code → 400 with error."""
    response = await client.post(
        "/register",
        data={
            "code": "FAKE-CODE-XXXX",
            "discord_username": "testuser",
            "password": "password123",
            "password2": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Invalid invite code" in response.text


# ---------------------------------------------------------------------------
# Vote page tests
# ---------------------------------------------------------------------------


async def test_vote_page_renders_for_eligible_member(
    client: AsyncClient,
    member_with_user: GuildMember,
    live_campaign_with_entries: Campaign,
):
    """GET /vote/{id} for eligible member → 200, contains vote form."""
    token = _make_token(member_with_user)
    response = await client.get(
        f"/vote/{live_campaign_with_entries.id}",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "vote-grid" in response.text
    assert "vote-form" in response.text
    # Should see all entries
    assert "Entry 1" in response.text


async def test_vote_page_shows_gallery_for_view_only_member(
    client: AsyncClient,
    live_campaign_with_entries: Campaign,
    db_session: AsyncSession,
):
    """GET /vote/{id} for member below min_rank_to_vote → shows entries, no vote form."""
    # Create initiate member (rank level 1, below min_rank_to_vote=2)
    rank = GuildRank(name="Initiate_vo", level=1)
    db_session.add(rank)
    await db_session.flush()
    user = User(password_hash=hash_password("x"), is_active=True)
    db_session.add(user)
    await db_session.flush()
    low_member = GuildMember(
        user_id=user.id,
        discord_username="low_rank_user",
        rank_id=rank.id,
    )
    db_session.add(low_member)
    await db_session.flush()
    low_member.rank = rank

    token = _make_token(low_member)
    response = await client.get(
        f"/vote/{live_campaign_with_entries.id}",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "vote-form" not in response.text
    assert "vote-grid" in response.text
    assert "rank is not high enough" in response.text.lower() or "view_only" in response.text.lower()


async def test_vote_page_shows_results_after_voting(
    client: AsyncClient,
    member_with_user: GuildMember,
    live_campaign_with_entries: Campaign,
    db_session: AsyncSession,
):
    """GET /vote/{id} after casting vote → shows 'voted' state with standings."""
    from sv_common.db.models import Vote

    # Manually insert votes for the member
    entries = live_campaign_with_entries.entries
    for i, entry in enumerate(entries[:3]):
        vote = Vote(
            campaign_id=live_campaign_with_entries.id,
            member_id=member_with_user.id,
            entry_id=entry.id,
            rank=i + 1,
        )
        db_session.add(vote)
    await db_session.flush()

    token = _make_token(member_with_user)
    response = await client.get(
        f"/vote/{live_campaign_with_entries.id}",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "vote is in" in response.text.lower() or "Your vote" in response.text
    assert "vote-form" not in response.text


async def test_vote_page_shows_results_when_closed(
    client: AsyncClient,
    live_campaign_with_entries: Campaign,
    db_session: AsyncSession,
):
    """GET /vote/{id} for closed campaign → shows final results."""
    # Close the campaign
    live_campaign_with_entries.status = "closed"
    await db_session.flush()

    response = await client.get(f"/vote/{live_campaign_with_entries.id}")
    assert response.status_code == 200
    assert "Results" in response.text
    assert "vote-form" not in response.text


async def test_vote_page_not_found_returns_404(client: AsyncClient):
    """GET /vote/99999 → 404 page."""
    response = await client.get("/vote/99999")
    assert response.status_code == 404


async def test_vote_page_anonymous_on_live_campaign(
    client: AsyncClient,
    live_campaign_with_entries: Campaign,
):
    """GET /vote/{id} without auth → shows public/login prompt state."""
    response = await client.get(f"/vote/{live_campaign_with_entries.id}")
    assert response.status_code == 200
    # Should show a login prompt or public state, not the vote form
    assert "vote-form" not in response.text


# ---------------------------------------------------------------------------
# Admin page tests
# ---------------------------------------------------------------------------


async def test_admin_campaigns_requires_officer_rank(
    client: AsyncClient,
    member_with_user: GuildMember,
):
    """GET /admin/campaigns for Member-rank user → redirect to login (not officer)."""
    token = _make_token(member_with_user)
    response = await client.get(
        "/admin/campaigns",
        cookies=_auth_cookies(token),
        follow_redirects=False,
    )
    # Should redirect (not authorized)
    assert response.status_code == 302


async def test_admin_campaigns_requires_auth(client: AsyncClient):
    """GET /admin/campaigns without auth → redirects to login."""
    response = await client.get("/admin/campaigns", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


async def test_admin_campaigns_accessible_by_officer(
    client: AsyncClient,
    officer_member_with_user: GuildMember,
):
    """GET /admin/campaigns for Officer → 200."""
    token = _make_token(officer_member_with_user)
    response = await client.get(
        "/admin/campaigns",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "Campaigns" in response.text


async def test_admin_roster_requires_officer_rank(
    client: AsyncClient,
    member_with_user: GuildMember,
):
    """GET /admin/roster for Member-rank → redirect (not officer)."""
    token = _make_token(member_with_user)
    response = await client.get(
        "/admin/roster",
        cookies=_auth_cookies(token),
        follow_redirects=False,
    )
    assert response.status_code == 302


async def test_admin_roster_accessible_by_officer(
    client: AsyncClient,
    officer_member_with_user: GuildMember,
):
    """GET /admin/roster for Officer → 200."""
    token = _make_token(officer_member_with_user)
    response = await client.get(
        "/admin/roster",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "Roster" in response.text


async def test_admin_new_campaign_form_accessible_by_officer(
    client: AsyncClient,
    officer_member_with_user: GuildMember,
):
    """GET /admin/campaigns/new for Officer → 200, contains form."""
    token = _make_token(officer_member_with_user)
    response = await client.get(
        "/admin/campaigns/new",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert "title" in response.text.lower()
    assert "duration" in response.text.lower()


async def test_admin_edit_campaign_accessible_by_officer(
    client: AsyncClient,
    officer_member_with_user: GuildMember,
    live_campaign_with_entries: Campaign,
):
    """GET /admin/campaigns/{id}/edit for Officer → 200."""
    token = _make_token(officer_member_with_user)
    response = await client.get(
        f"/admin/campaigns/{live_campaign_with_entries.id}/edit",
        cookies=_auth_cookies(token),
    )
    assert response.status_code == 200
    assert live_campaign_with_entries.title in response.text
