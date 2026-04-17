"""Unit tests for migration 0132 — Phase H recipe bridge (blizzard_item_id on item_recipe_links).

Tests verify:
  - blizzard_item_id column added to guild_identity.item_recipe_links
  - sp_rebuild_item_recipes no longer JOINs guild_identity.wow_items
  - sp_rebuild_item_recipes still promotes unclassified→crafted
  - sp_rebuild_item_recipes still links crafted items to active season

No database connection required — tests parse the migration SQL as text.
"""

import importlib.util
import types
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "alembic" / "versions" / "0132_phase_h_item_recipe_links_blizzard_item_id.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0132", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def upgrade_sql(tmp_path_factory):
    """Capture all SQL strings passed to op.execute() during upgrade()."""
    sql_parts: list[str] = []

    class _FakeOp:
        @staticmethod
        def execute(sql: str):
            sql_parts.append(sql)

    mod = _load_migration()
    orig_op = mod.__dict__.get("op")
    mod.__dict__["op"] = _FakeOp
    try:
        mod.upgrade()
    finally:
        if orig_op is not None:
            mod.__dict__["op"] = orig_op
    return "\n".join(sql_parts)


class TestSchemaChange:
    def test_adds_blizzard_item_id_column(self, upgrade_sql):
        """Migration must ADD COLUMN blizzard_item_id to item_recipe_links."""
        assert "blizzard_item_id" in upgrade_sql
        assert "item_recipe_links" in upgrade_sql
        assert "ADD COLUMN" in upgrade_sql

    def test_backfills_from_wow_items(self, upgrade_sql):
        """Migration must UPDATE item_recipe_links from wow_items to backfill."""
        assert "UPDATE guild_identity.item_recipe_links" in upgrade_sql
        assert "guild_identity.wow_items" in upgrade_sql


class TestSpRebuildItemRecipesPhaseH:
    def _get_block(self, upgrade_sql) -> str:
        idx = upgrade_sql.find("sp_rebuild_item_recipes")
        return upgrade_sql[idx:idx + 2000]

    def test_procedure_defined(self, upgrade_sql):
        assert "sp_rebuild_item_recipes" in upgrade_sql

    def test_no_wow_items_join_in_insert(self, upgrade_sql):
        """Core goal: sproc must not JOIN guild_identity.wow_items to resolve blizzard_item_id."""
        block = self._get_block(upgrade_sql)
        insert_start = block.find("INSERT INTO enrichment.item_recipes")
        insert_end = block.find("GET DIAGNOSTICS", insert_start)
        insert_block = block[insert_start:insert_end]
        assert "guild_identity.wow_items" not in insert_block

    def test_uses_irl_blizzard_item_id_directly(self, upgrade_sql):
        """Sproc must read blizzard_item_id from item_recipe_links directly."""
        block = self._get_block(upgrade_sql)
        assert "irl.blizzard_item_id" in block

    def test_still_promotes_unclassified_to_crafted(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'unclassified'" in block
        assert "item_category = 'crafted'" in block

    def test_still_links_to_active_season(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_seasons" in block
        assert "is_active = TRUE" in block
        assert "ON CONFLICT DO NOTHING" in block

    def test_filters_null_blizzard_item_id(self, upgrade_sql):
        """Sproc must skip rows where blizzard_item_id is NULL (pre-backfill safety)."""
        block = self._get_block(upgrade_sql)
        assert "blizzard_item_id IS NOT NULL" in block
