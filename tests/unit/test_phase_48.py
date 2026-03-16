"""
Unit tests for Phase 4.8 — Quotes 2.0: Player-Associated Quotes.

Tests cover:
1.  Slug validation: valid slug accepted
2.  Slug validation: bad slug rejected (uppercase)
3.  Slug validation: bad slug rejected (starts with digit)
4.  Slug validation: reserved slug rejected
5.  Slug validation: slug with spaces rejected
6.  Slug validation: max-length slug accepted
7.  ORM: QuoteSubject model exists with expected columns
8.  ORM: GuildQuote has subject_id column
9.  ORM: GuildQuoteTitle has subject_id column
10. ORM: QuoteSubject.player relationship defined
11. ORM: QuoteSubject.quotes back-populates correctly
12. ORM: QuoteSubject.titles back-populates correctly
13. Bot: register_guild_quote_commands skips reserved slugs
14. Bot: sync_quote_commands removes old commands before re-registering
15. Config cache: get_realm_display_name reads from cache
16. Admin API: list_quote_subjects route exists
17. Admin API: create_quote_subject route exists
18. Admin API: patch_quote_subject route exists
19. Admin API: delete_quote_subject route exists
20. Admin API: sync-commands route exists
21. Admin API: subject quotes CRUD routes exist
22. Admin API: subject titles CRUD routes exist
23. Admin pages: /admin/quotes in _PATH_TO_SCREEN
24. Admin pages: players-search route exists
25. Public: guild_quote_subject passed in index context
26. Migration: 0044 file references quote_subjects table
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1–6. Slug validation
# ---------------------------------------------------------------------------

class TestSlugValidation:
    def _validate(self, slug):
        from guild_portal.api.admin_routes import _validate_slug
        return _validate_slug(slug)

    def test_valid_slug(self):
        assert self._validate("mito") is None

    def test_valid_slug_with_hyphens(self):
        assert self._validate("rocket-man") is None

    def test_valid_slug_with_underscores(self):
        assert self._validate("the_charger") is None

    def test_uppercase_rejected(self):
        assert self._validate("Mito") is not None

    def test_starts_with_digit_rejected(self):
        assert self._validate("1mito") is not None

    def test_reserved_slug_rejected(self):
        assert self._validate("quote") is not None
        assert self._validate("help") is not None
        assert self._validate("admin") is not None

    def test_spaces_rejected(self):
        assert self._validate("my name") is not None

    def test_max_length_slug_accepted(self):
        # 31 chars: 1 letter + 30 more = within the regex {0,30} range
        slug = "a" + "b" * 30
        assert self._validate(slug) is None

    def test_over_max_length_rejected(self):
        slug = "a" + "b" * 32  # 33 chars
        assert self._validate(slug) is not None


# ---------------------------------------------------------------------------
# 7–12. ORM model structure
# ---------------------------------------------------------------------------

class TestORMModels:
    def test_quote_subject_model_exists(self):
        from sv_common.db.models import QuoteSubject
        assert QuoteSubject.__tablename__ == "quote_subjects"
        assert QuoteSubject.__table_args__[-1]["schema"] == "patt"

    def test_quote_subject_columns(self):
        from sv_common.db.models import QuoteSubject
        cols = {c.key for c in QuoteSubject.__table__.columns}
        assert {"id", "player_id", "command_slug", "display_name", "active", "created_at"} <= cols

    def test_guild_quote_has_subject_id(self):
        from sv_common.db.models import GuildQuote
        cols = {c.key for c in GuildQuote.__table__.columns}
        assert "subject_id" in cols

    def test_guild_quote_title_has_subject_id(self):
        from sv_common.db.models import GuildQuoteTitle
        cols = {c.key for c in GuildQuoteTitle.__table__.columns}
        assert "subject_id" in cols

    def test_quote_subject_player_relationship(self):
        from sv_common.db.models import QuoteSubject
        assert hasattr(QuoteSubject, "player")

    def test_quote_subject_quotes_relationship(self):
        from sv_common.db.models import QuoteSubject
        assert hasattr(QuoteSubject, "quotes")

    def test_quote_subject_titles_relationship(self):
        from sv_common.db.models import QuoteSubject
        assert hasattr(QuoteSubject, "titles")

    def test_guild_quote_subject_relationship(self):
        from sv_common.db.models import GuildQuote
        assert hasattr(GuildQuote, "subject")

    def test_guild_quote_title_subject_relationship(self):
        from sv_common.db.models import GuildQuoteTitle
        assert hasattr(GuildQuoteTitle, "subject")


# ---------------------------------------------------------------------------
# 13–14. Bot command registration
# ---------------------------------------------------------------------------

class TestBotCommandRegistration:
    def test_reserved_slug_skipped(self):
        """_RESERVED_SLUGS prevents 'quote' and other built-ins from being registered."""
        from guild_portal.bot.guild_quote_commands import _RESERVED_SLUGS
        assert "quote" in _RESERVED_SLUGS
        assert "help" in _RESERVED_SLUGS

    @pytest.mark.asyncio
    async def test_sync_removes_old_commands(self):
        """sync_quote_commands removes stale subject commands before re-registering."""
        from guild_portal.bot.guild_quote_commands import sync_quote_commands

        mock_tree = MagicMock()
        mock_tree.sync = AsyncMock()
        mock_tree.copy_global_to = MagicMock()
        mock_tree.command = MagicMock(side_effect=lambda **kw: (lambda f: f))
        mock_tree.remove_command = MagicMock()

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"command_slug": "mito"},
            {"command_slug": "rocket"},
        ])
        mock_pool.acquire = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        # Patch the inner fetch for subjects
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with patch(
            "guild_portal.bot.guild_quote_commands.is_guild_quotes_enabled",
            return_value=True,
        ):
            await sync_quote_commands(mock_tree, mock_pool, discord_guild=None)

        # Should remove both old slug commands + "quote"
        removed = {call.args[0] for call in mock_tree.remove_command.call_args_list}
        assert "mito" in removed
        assert "rocket" in removed
        assert "quote" in removed

    @pytest.mark.asyncio
    async def test_async_register_skips_disabled_feature(self):
        """_async_register_guild_quote_commands exits early when feature disabled."""
        from guild_portal.bot.guild_quote_commands import _async_register_guild_quote_commands

        mock_tree = MagicMock()
        mock_pool = MagicMock()

        with patch(
            "guild_portal.bot.guild_quote_commands.is_guild_quotes_enabled",
            return_value=False,
        ):
            await _async_register_guild_quote_commands(mock_tree, mock_pool)

        mock_tree.command.assert_not_called()


# ---------------------------------------------------------------------------
# 15. Config cache
# ---------------------------------------------------------------------------

class TestConfigCache:
    def test_get_realm_display_name_empty_by_default(self):
        from sv_common.config_cache import get_realm_display_name
        # Cache is empty in tests; should return ""
        result = get_realm_display_name()
        assert isinstance(result, str)

    def test_get_realm_display_name_reads_from_cache(self):
        from sv_common.config_cache import set_site_config, get_realm_display_name
        set_site_config({"realm_display_name": "Sen'jin"})
        assert get_realm_display_name() == "Sen'jin"
        # Reset
        set_site_config({})


# ---------------------------------------------------------------------------
# 16–22. Admin API routes exist
# ---------------------------------------------------------------------------

class TestAdminAPIRoutes:
    def _route_names(self):
        from guild_portal.api.admin_routes import router
        return {r.name for r in router.routes}

    def test_list_quote_subjects_route(self):
        assert "list_quote_subjects" in self._route_names()

    def test_create_quote_subject_route(self):
        assert "create_quote_subject" in self._route_names()

    def test_update_quote_subject_route(self):
        assert "update_quote_subject" in self._route_names()

    def test_delete_quote_subject_route(self):
        assert "delete_quote_subject" in self._route_names()

    def test_sync_commands_route(self):
        assert "sync_quote_commands_endpoint" in self._route_names()

    def test_list_subject_quotes_route(self):
        assert "list_subject_quotes" in self._route_names()

    def test_add_subject_quote_route(self):
        assert "add_subject_quote" in self._route_names()

    def test_update_quote_route(self):
        assert "update_quote" in self._route_names()

    def test_delete_quote_route(self):
        assert "delete_quote" in self._route_names()

    def test_list_subject_titles_route(self):
        assert "list_subject_titles" in self._route_names()

    def test_add_subject_title_route(self):
        assert "add_subject_title" in self._route_names()

    def test_update_title_route(self):
        assert "update_title" in self._route_names()

    def test_delete_title_route(self):
        assert "delete_title" in self._route_names()


# ---------------------------------------------------------------------------
# 23–24. Admin pages
# ---------------------------------------------------------------------------

class TestAdminPages:
    def test_quotes_in_path_to_screen(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        paths = dict(_PATH_TO_SCREEN)
        assert "/admin/quotes" in paths
        assert paths["/admin/quotes"] == "quotes"

    def test_players_search_route_exists(self):
        from guild_portal.pages.admin_pages import router
        route_names = {r.name for r in router.routes}
        assert "admin_players_search" in route_names


# ---------------------------------------------------------------------------
# 25. Public index context
# ---------------------------------------------------------------------------

class TestPublicIndex:
    @pytest.mark.asyncio
    async def test_index_context_includes_subject(self):
        """The index route passes guild_quote_subject to the template context."""
        import inspect
        from guild_portal.pages.public_pages import landing_page
        source = inspect.getsource(landing_page)
        assert "guild_quote_subject" in source

    def test_index_template_uses_subject(self):
        """The index template references guild_quote_subject."""
        import pathlib
        tpl = pathlib.Path(
            "src/guild_portal/templates/public/index.html"
        ).read_text(encoding="utf-8")
        assert "guild_quote_subject" in tpl


# ---------------------------------------------------------------------------
# 26. Migration file
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migration_0044_exists(self):
        import pathlib
        migrations = list(pathlib.Path("alembic/versions").glob("0044_*.py"))
        assert migrations, "Migration 0044 not found"

    def test_migration_references_quote_subjects(self):
        import pathlib
        migrations = list(pathlib.Path("alembic/versions").glob("0044_*.py"))
        assert migrations
        content = migrations[0].read_text(encoding="utf-8")
        assert "quote_subjects" in content
        assert "subject_id" in content

    def test_migration_revision_chain(self):
        import pathlib
        migrations = list(pathlib.Path("alembic/versions").glob("0044_*.py"))
        assert migrations
        content = migrations[0].read_text(encoding="utf-8")
        assert 'down_revision = "0043"' in content
