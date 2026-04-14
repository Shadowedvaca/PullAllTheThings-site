"""Unit tests for Phase C — viz schema view definitions.

These tests verify the migration SQL structure: that each view is defined,
uses the right source tables, exposes the columns Phase D will depend on,
and correctly filters junk rows and scopes by item category.

No database connection required — tests parse the migration file as text and
validate structural invariants that would catch regressions during Phase D
development.
"""

import importlib.util
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load migration module
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "alembic" / "versions" / "0106_viz_views.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0106", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def _sql_from_upgrade(migration_mod) -> str:
    """Extract all SQL strings passed to op.execute() in upgrade()."""
    calls: list[str] = []

    class _Capture:
        def execute(self, sql: str):
            calls.append(sql)

    original_upgrade = migration_mod.upgrade
    import alembic.operations
    real_op = migration_mod.__dict__.get("op")

    # Monkey-patch op to capture SQL
    import types
    fake_op = types.SimpleNamespace(execute=lambda sql: calls.append(sql))
    migration_mod.__dict__["op"] = fake_op
    try:
        migration_mod.upgrade()
    finally:
        if real_op is not None:
            migration_mod.__dict__["op"] = real_op

    return "\n".join(calls)


@pytest.fixture(scope="module")
def upgrade_sql(migration):
    return _sql_from_upgrade(migration)


# ---------------------------------------------------------------------------
# Migration metadata
# ---------------------------------------------------------------------------


class TestMigrationMetadata:
    def test_revision(self, migration):
        assert migration.revision == "0106"

    def test_down_revision(self, migration):
        assert migration.down_revision == "0105"

    def test_has_upgrade(self, migration):
        assert callable(migration.upgrade)

    def test_has_downgrade(self, migration):
        assert callable(migration.downgrade)


# ---------------------------------------------------------------------------
# viz.slot_items
# ---------------------------------------------------------------------------


class TestSlotItemsView:
    def test_view_created(self, upgrade_sql):
        assert "viz.slot_items" in upgrade_sql

    def test_reads_enrichment_items(self, upgrade_sql):
        # Extract only the slot_items block
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "enrichment.items" in block

    def test_joins_enrichment_item_sources(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "enrichment.item_sources" in block

    def test_filters_junk(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "is_junk" in block

    def test_exposes_blizzard_item_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "blizzard_item_id" in block

    def test_exposes_item_category(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "item_category" in block

    def test_exposes_quality_tracks(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "quality_tracks" in block

    def test_exposes_slot_type(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "slot_type" in block

    def test_exposes_armor_type(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "armor_type" in block

    def test_exposes_instance_type(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "instance_type" in block

    def test_exposes_blizzard_instance_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.slot_items")
        block = upgrade_sql[idx:idx + 1000]
        assert "blizzard_instance_id" in block


# ---------------------------------------------------------------------------
# viz.tier_piece_sources
# ---------------------------------------------------------------------------


class TestTierPieceSourcesView:
    def test_view_created(self, upgrade_sql):
        assert "viz.tier_piece_sources" in upgrade_sql

    def test_reads_enrichment_items(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "enrichment.items" in block

    def test_uses_enrichment_item_sources(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "enrichment.item_sources" in block

    def test_bridges_via_tier_token_attrs(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "tier_token_attrs" in block

    def test_filters_junk(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "is_junk" in block

    def test_filters_tier_category(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "item_category" in block
        assert "'tier'" in block

    def test_filters_tier_slots(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "head" in block
        assert "shoulder" in block
        assert "chest" in block
        assert "hands" in block
        assert "legs" in block

    def test_exposes_tier_piece_blizzard_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "tier_piece_blizzard_id" in block

    def test_exposes_boss_name(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "boss_name" in block

    def test_exposes_instance_name(self, upgrade_sql):
        idx = upgrade_sql.find("viz.tier_piece_sources")
        block = upgrade_sql[idx:idx + 1200]
        assert "instance_name" in block


# ---------------------------------------------------------------------------
# viz.crafters_by_item
# ---------------------------------------------------------------------------


class TestCraftersByItemView:
    def test_view_created(self, upgrade_sql):
        assert "viz.crafters_by_item" in upgrade_sql

    def test_reads_enrichment_item_recipes(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "enrichment.item_recipes" in block

    def test_joins_guild_identity_recipes(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "guild_identity.recipes" in block

    def test_joins_wow_characters(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "wow_characters" in block

    def test_filters_in_guild(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "in_guild" in block

    def test_joins_guild_ranks(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "guild_ranks" in block

    def test_exposes_blizzard_item_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "blizzard_item_id" in block

    def test_exposes_character_name(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "character_name" in block

    def test_exposes_rank_level(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "rank_level" in block

    def test_orders_by_rank_desc(self, upgrade_sql):
        idx = upgrade_sql.find("viz.crafters_by_item")
        block = upgrade_sql[idx:idx + 1200]
        assert "DESC" in block.upper()


# ---------------------------------------------------------------------------
# viz.bis_recommendations
# ---------------------------------------------------------------------------


class TestBisRecommendationsView:
    def test_view_created(self, upgrade_sql):
        assert "viz.bis_recommendations" in upgrade_sql

    def test_reads_enrichment_bis_entries(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "enrichment.bis_entries" in block

    def test_joins_enrichment_items(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "enrichment.items" in block

    def test_joins_bis_list_sources(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "bis_list_sources" in block

    def test_exposes_source_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "source_id" in block

    def test_exposes_spec_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "spec_id" in block

    def test_exposes_hero_talent_id(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "hero_talent_id" in block

    def test_exposes_slot(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "slot" in block

    def test_exposes_item_category(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "item_category" in block

    def test_exposes_quality_tracks(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "quality_tracks" in block

    def test_quality_tracks_excludes_junk(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "is_junk" in block

    def test_exposes_source_origin(self, upgrade_sql):
        idx = upgrade_sql.find("viz.bis_recommendations")
        block = upgrade_sql[idx:idx + 1500]
        assert "source_origin" in block


# ---------------------------------------------------------------------------
# Downgrade covers all views
# ---------------------------------------------------------------------------


class TestDowngrade:
    def _downgrade_sql(self, migration_mod) -> str:
        calls: list[str] = []
        import types
        fake_op = types.SimpleNamespace(execute=lambda sql: calls.append(sql))
        real_op = migration_mod.__dict__.get("op")
        migration_mod.__dict__["op"] = fake_op
        try:
            migration_mod.downgrade()
        finally:
            if real_op is not None:
                migration_mod.__dict__["op"] = real_op
        return "\n".join(calls)

    def test_drops_slot_items(self, migration):
        sql = self._downgrade_sql(migration)
        assert "viz.slot_items" in sql

    def test_drops_tier_piece_sources(self, migration):
        sql = self._downgrade_sql(migration)
        assert "viz.tier_piece_sources" in sql

    def test_drops_crafters_by_item(self, migration):
        sql = self._downgrade_sql(migration)
        assert "viz.crafters_by_item" in sql

    def test_drops_bis_recommendations(self, migration):
        sql = self._downgrade_sql(migration)
        assert "viz.bis_recommendations" in sql
