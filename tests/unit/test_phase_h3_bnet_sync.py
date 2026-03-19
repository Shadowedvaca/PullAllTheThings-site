"""
Unit tests for Phase H.3 — POST /api/v1/me/bnet-sync endpoint and
GET /api/v1/me/characters out-of-guild extension.
"""

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(player_id=1):
    player = MagicMock()
    player.id = player_id
    return player


def _make_request(next_url=None):
    request = MagicMock()
    request.app.state.guild_sync_pool = MagicMock()
    params = {}
    if next_url:
        params["next"] = next_url
    request.query_params.get = lambda key, default=None: params.get(key, default)
    return request


def _make_db(bnet_account=None):
    """Return a mock AsyncSession that returns bnet_account on scalar_one_or_none()."""
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = bnet_account
    db.execute.return_value = execute_result
    return db


# ---------------------------------------------------------------------------
# POST /api/v1/me/bnet-sync — not linked
# ---------------------------------------------------------------------------


class TestBnetSyncNotLinked:
    @pytest.mark.asyncio
    async def test_returns_redirect_when_no_bnet_account(self):
        """Player with no battlenet_accounts row gets redirect to OAuth."""
        from guild_portal.api.member_routes import member_bnet_sync

        request = _make_request()
        player = _make_player()
        db = _make_db(bnet_account=None)

        result = await member_bnet_sync(
            request=request,
            current_player=player,
            db=db,
        )

        import json
        body = json.loads(result.body)
        assert body["ok"] is True
        assert "redirect" in body
        assert body["redirect"].startswith("/auth/battlenet")
        assert "next=" in body["redirect"]

    @pytest.mark.asyncio
    async def test_redirect_defaults_to_my_characters(self):
        """Default next destination is /my-characters when no next param given."""
        from guild_portal.api.member_routes import member_bnet_sync

        request = _make_request()  # no next param
        player = _make_player()
        db = _make_db(bnet_account=None)

        result = await member_bnet_sync(request=request, current_player=player, db=db)

        import json
        body = json.loads(result.body)
        assert "/my-characters" in body["redirect"]


# ---------------------------------------------------------------------------
# POST /api/v1/me/bnet-sync — expired token
# ---------------------------------------------------------------------------


class TestBnetSyncExpiredToken:
    @pytest.mark.asyncio
    async def test_returns_redirect_when_token_expired(self):
        """Expired token returns redirect rather than attempting sync."""
        from guild_portal.api.member_routes import member_bnet_sync

        request = _make_request()
        player = _make_player()

        bnet_account = MagicMock()
        db = _make_db(bnet_account=bnet_account)

        with patch(
            "guild_portal.api.member_routes.member_bnet_sync.__wrapped__"
            if hasattr(member_bnet_sync, "__wrapped__") else
            "sv_common.guild_sync.bnet_character_sync.get_valid_access_token",
            new=AsyncMock(return_value=None),
        ):
            # Patch at import-time name used inside the function
            with patch(
                "sv_common.guild_sync.bnet_character_sync.get_valid_access_token",
                new=AsyncMock(return_value=None),
            ):
                result = await member_bnet_sync(
                    request=request,
                    current_player=player,
                    db=db,
                )

        import json
        body = json.loads(result.body)
        assert body["ok"] is True
        assert "redirect" in body


# ---------------------------------------------------------------------------
# POST /api/v1/me/bnet-sync — valid token syncs
# ---------------------------------------------------------------------------


class TestBnetSyncValidToken:
    @pytest.mark.asyncio
    async def test_syncs_and_returns_stats_when_token_valid(self):
        """Valid token causes sync and returns stats dict."""
        from guild_portal.api.member_routes import member_bnet_sync

        request = _make_request()
        player = _make_player()

        bnet_account = MagicMock()
        db = _make_db(bnet_account=bnet_account)

        mock_stats = {"upserted": 3, "linked": 1, "skipped": 0}

        with patch(
            "sv_common.guild_sync.bnet_character_sync.get_valid_access_token",
            new=AsyncMock(return_value="valid-token-abc"),
        ):
            with patch(
                "sv_common.guild_sync.bnet_character_sync.sync_bnet_characters",
                new=AsyncMock(return_value=mock_stats),
            ):
                result = await member_bnet_sync(
                    request=request,
                    current_player=player,
                    db=db,
                )

        import json
        body = json.loads(result.body)
        assert body["ok"] is True
        assert "redirect" not in body
        assert body["data"] == mock_stats


# ---------------------------------------------------------------------------
# next param whitelist
# ---------------------------------------------------------------------------


class TestNextParamWhitelist:
    def test_allowed_next_set_contains_expected_values(self):
        """ALLOWED_NEXT whitelist prevents open redirect to arbitrary URLs."""
        src = inspect.getsource(
            __import__("guild_portal.api.member_routes", fromlist=["member_bnet_sync"]).member_bnet_sync
        )
        assert "ALLOWED_NEXT" in src
        assert '"/my-characters"' in src
        assert '"/profile"' in src
        assert '"/"' in src

    def test_unknown_next_falls_back_to_my_characters(self):
        """Source check: unknown next values are replaced with /my-characters."""
        from guild_portal.api.member_routes import member_bnet_sync
        src = inspect.getsource(member_bnet_sync)
        assert "next_url = " in src
        assert '"/my-characters"' in src


# ---------------------------------------------------------------------------
# GET /api/v1/me/characters — out-of-guild extension (source inspection)
# ---------------------------------------------------------------------------


class TestMeCharactersOutOfGuildExtension:
    def test_get_my_characters_queries_out_of_guild(self):
        """GET /me/characters now includes an out-of-guild character query."""
        from guild_portal.api.member_routes import get_my_characters
        src = inspect.getsource(get_my_characters)
        assert "out_of_guild_characters" in src
        assert "in_guild == False" in src

    def test_get_my_characters_returns_bnet_flags(self):
        """GET /me/characters returns bnet_linked and bnet_token_expired flags."""
        from guild_portal.api.member_routes import get_my_characters
        src = inspect.getsource(get_my_characters)
        assert "bnet_linked" in src
        assert "bnet_token_expired" in src

    def test_get_my_characters_queries_battlenet_account(self):
        """GET /me/characters queries BattlenetAccount for the current player."""
        from guild_portal.api.member_routes import get_my_characters
        src = inspect.getsource(get_my_characters)
        assert "BattlenetAccount" in src
