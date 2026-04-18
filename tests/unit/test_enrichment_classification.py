"""Unit tests for migration 0107 — enrichment classification overhaul.

Tests verify:
  - item_category CHECK constraint updated to new values
  - enrichment.item_seasons table created
  - sp_rebuild_item_seasons procedure exists with correct logic
  - sp_update_item_categories rewritten (no Wowhead HTML, uses tier_token_attrs + item_seasons)
  - sp_rebuild_item_recipes includes unclassified→crafted promotion
  - sp_rebuild_all includes item_seasons step
  - viz.slot_items recreated with item_seasons JOIN for season filtering

No database connection required — tests parse the migration SQL as text.
"""

import importlib.util
import types
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "alembic" / "versions" / "0107_enrichment_classification_overhaul.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0107", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def _capture_sql(migration_mod, fn_name: str) -> str:
    """Capture all SQL strings passed to op.execute() in upgrade() or downgrade()."""
    calls: list[str] = []
    fake_op = types.SimpleNamespace(execute=lambda sql: calls.append(sql))
    real_op = migration_mod.__dict__.get("op")
    migration_mod.__dict__["op"] = fake_op
    try:
        getattr(migration_mod, fn_name)()
    finally:
        if real_op is not None:
            migration_mod.__dict__["op"] = real_op
    return "\n".join(calls)


@pytest.fixture(scope="module")
def upgrade_sql(migration):
    return _capture_sql(migration, "upgrade")


@pytest.fixture(scope="module")
def downgrade_sql(migration):
    return _capture_sql(migration, "downgrade")


# ---------------------------------------------------------------------------
# Migration metadata
# ---------------------------------------------------------------------------


class TestMigrationMetadata:
    def test_revision(self, migration):
        assert migration.revision == "0107"

    def test_down_revision(self, migration):
        assert migration.down_revision == "0106"

    def test_has_upgrade(self, migration):
        assert callable(migration.upgrade)

    def test_has_downgrade(self, migration):
        assert callable(migration.downgrade)


# ---------------------------------------------------------------------------
# item_category CHECK constraint
# ---------------------------------------------------------------------------


class TestItemCategoryConstraint:
    def test_new_values_in_constraint(self, upgrade_sql):
        assert "raid" in upgrade_sql
        assert "dungeon" in upgrade_sql
        assert "world_boss" in upgrade_sql
        assert "unclassified" in upgrade_sql

    def test_drops_old_constraint(self, upgrade_sql):
        assert "DROP CONSTRAINT items_item_category_check" in upgrade_sql

    def test_adds_new_constraint(self, upgrade_sql):
        assert "ADD CONSTRAINT items_item_category_check" in upgrade_sql

    def test_new_default_unclassified(self, upgrade_sql):
        assert "SET DEFAULT 'unclassified'" in upgrade_sql

    def test_migrates_unknown_to_unclassified(self, upgrade_sql):
        # Data migration step must precede constraint change
        assert "'unknown'" in upgrade_sql
        assert "'unclassified'" in upgrade_sql

    def test_migrates_drop_to_unclassified(self, upgrade_sql):
        assert "'drop'" in upgrade_sql

    def test_downgrade_restores_old_values(self, downgrade_sql):
        assert "'tier','catalyst','crafted','drop','unknown'" in downgrade_sql

    def test_downgrade_restores_default_unknown(self, downgrade_sql):
        assert "SET DEFAULT 'unknown'" in downgrade_sql


# ---------------------------------------------------------------------------
# enrichment.item_seasons bridge table
# ---------------------------------------------------------------------------


class TestItemSeasonsTable:
    def test_table_created(self, upgrade_sql):
        assert "enrichment.item_seasons" in upgrade_sql
        assert "CREATE TABLE" in upgrade_sql

    def test_has_blizzard_item_id_fk(self, upgrade_sql):
        idx = upgrade_sql.find("enrichment.item_seasons")
        block = upgrade_sql[idx:idx + 600]
        assert "blizzard_item_id" in block
        assert "REFERENCES enrichment.items" in block

    def test_has_season_id_fk(self, upgrade_sql):
        idx = upgrade_sql.find("enrichment.item_seasons")
        block = upgrade_sql[idx:idx + 600]
        assert "season_id" in block
        assert "REFERENCES patt.raid_seasons" in block

    def test_has_cascade_on_delete(self, upgrade_sql):
        idx = upgrade_sql.find("enrichment.item_seasons")
        block = upgrade_sql[idx:idx + 600]
        assert "ON DELETE CASCADE" in block

    def test_has_primary_key(self, upgrade_sql):
        idx = upgrade_sql.find("enrichment.item_seasons")
        block = upgrade_sql[idx:idx + 600]
        assert "PRIMARY KEY" in block

    def test_has_season_index(self, upgrade_sql):
        assert "ix_enrichment_item_seasons_season" in upgrade_sql

    def test_dropped_in_downgrade(self, downgrade_sql):
        assert "DROP TABLE IF EXISTS enrichment.item_seasons" in downgrade_sql


# ---------------------------------------------------------------------------
# sp_rebuild_item_seasons
# ---------------------------------------------------------------------------


class TestSpRebuildItemSeasons:
    def _get_block(self, upgrade_sql) -> str:
        idx = upgrade_sql.find("sp_rebuild_item_seasons")
        return upgrade_sql[idx:idx + 5000]

    def test_procedure_created(self, upgrade_sql):
        assert "enrichment.sp_rebuild_item_seasons" in upgrade_sql

    def test_truncates_item_seasons(self, upgrade_sql):
        assert "TRUNCATE enrichment.item_seasons" in self._get_block(upgrade_sql)

    def test_populates_raid_items_via_instance_id(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "current_raid_ids" in block
        assert "instance_type = 'raid'" in block

    def test_populates_dungeon_items_via_instance_id(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "current_instance_ids" in block
        assert "instance_type = 'dungeon'" in block

    def test_populates_tier_via_token_chain(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "guild_identity.tier_token_attrs" in block
        assert "token_item_id" in block

    def test_tier_restricted_to_tier_slots(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "'head'" in block
        assert "'shoulder'" in block
        assert "'chest'" in block
        assert "'hands'" in block
        assert "'legs'" in block

    def test_populates_catalyst_via_quality_track(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        # Catalyst items use quality_track='C' since tier_set_suffix not yet populated
        assert "quality_track = 'C'" in block

    def test_populates_crafted_items(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_recipes" in block
        assert "is_active = TRUE" in block

    def test_uses_on_conflict_do_nothing(self, upgrade_sql):
        assert "ON CONFLICT DO NOTHING" in self._get_block(upgrade_sql)

    def test_dropped_in_downgrade(self, downgrade_sql):
        assert "DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_item_seasons()" in downgrade_sql


# ---------------------------------------------------------------------------
# sp_update_item_categories (rewritten)
# ---------------------------------------------------------------------------


class TestSpUpdateItemCategories:
    def _get_block(self, upgrade_sql) -> str:
        idx = upgrade_sql.find("sp_update_item_categories")
        return upgrade_sql[idx:idx + 6000]

    def test_procedure_exists(self, upgrade_sql):
        assert "sp_update_item_categories" in upgrade_sql

    def test_resets_to_unclassified(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "'unclassified'" in block

    def test_uses_tier_token_attrs_for_tier(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "guild_identity.tier_token_attrs" in block

    def test_no_wowhead_tooltip_html_heuristic(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        # The old heuristic used wowhead_tooltip_html LIKE '%/item-set=%'
        # This MUST NOT appear in the new procedure
        assert "wowhead_tooltip_html" not in block
        assert "item-set=" not in block

    def test_tier_requires_item_seasons_link(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_seasons" in block

    def test_classifies_raid_category(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'raid'" in block

    def test_classifies_dungeon_category(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'dungeon'" in block

    def test_classifies_world_boss_category(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'world_boss'" in block

    def test_classifies_tier_category(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'tier'" in block

    def test_classifies_catalyst_category(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'catalyst'" in block

    def test_no_drop_category(self, upgrade_sql):
        # 'drop' must be replaced by 'raid'/'dungeon'/'world_boss'
        block = self._get_block(upgrade_sql)
        assert "item_category = 'drop'" not in block


# ---------------------------------------------------------------------------
# sp_rebuild_item_recipes (updated)
# ---------------------------------------------------------------------------


class TestSpRebuildItemRecipes:
    def _get_block(self, upgrade_sql) -> str:
        idx = upgrade_sql.find("sp_rebuild_item_recipes")
        return upgrade_sql[idx:idx + 2000]

    def test_procedure_exists(self, upgrade_sql):
        assert "sp_rebuild_item_recipes" in upgrade_sql

    def test_promotes_unclassified_to_crafted(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "item_category = 'unclassified'" in block
        assert "item_category = 'crafted'" in block

    def test_links_crafted_to_active_season(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_seasons" in block
        assert "is_active = TRUE" in block

    def test_uses_on_conflict_do_nothing(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "ON CONFLICT DO NOTHING" in block


# ---------------------------------------------------------------------------
# sp_rebuild_all (updated)
# ---------------------------------------------------------------------------


class TestSpRebuildAll:
    def _get_block(self, upgrade_sql) -> str:
        idx = upgrade_sql.find("sp_rebuild_all")
        return upgrade_sql[idx:idx + 1500]

    def test_calls_rebuild_item_seasons(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "sp_rebuild_item_seasons" in block

    def test_item_seasons_before_category_update(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        seasons_pos = block.find("sp_rebuild_item_seasons")
        category_pos = block.find("sp_update_item_categories")
        assert seasons_pos < category_pos, (
            "sp_rebuild_item_seasons must be called before sp_update_item_categories"
        )

    def test_reports_seasons_count(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_seasons" in block


# ---------------------------------------------------------------------------
# viz.slot_items (updated)
# ---------------------------------------------------------------------------


class TestVizSlotItemsUpdated:
    def _get_block(self, upgrade_sql) -> str:
        # Get the CREATE VIEW block from upgrade (after the DROP)
        create_idx = upgrade_sql.rfind("CREATE VIEW viz.slot_items")
        return upgrade_sql[create_idx:create_idx + 1200]

    def test_view_recreated(self, upgrade_sql):
        assert "CREATE VIEW viz.slot_items" in upgrade_sql

    def test_old_view_dropped(self, upgrade_sql):
        assert "DROP VIEW IF EXISTS viz.slot_items" in upgrade_sql

    def test_joins_item_seasons(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "enrichment.item_seasons" in block

    def test_filters_to_active_season(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "patt.raid_seasons" in block
        assert "is_active = TRUE" in block

    def test_still_exposes_all_phase_d_columns(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        for col in [
            "blizzard_item_id", "name", "icon_url", "slot_type", "armor_type",
            "primary_stat", "item_category", "tier_set_suffix", "quality_track",
            "instance_type", "encounter_name", "instance_name",
            "blizzard_instance_id", "quality_tracks", "is_junk",
        ]:
            assert col in block, f"viz.slot_items missing column: {col}"

    def test_still_left_joins_item_sources(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "LEFT JOIN enrichment.item_sources" in block

    def test_still_filters_junk(self, upgrade_sql):
        block = self._get_block(upgrade_sql)
        assert "is_junk" in block

    def test_downgrade_restores_view_without_seasons_join(self, downgrade_sql):
        create_idx = downgrade_sql.find("CREATE VIEW viz.slot_items")
        block = downgrade_sql[create_idx:create_idx + 1000]
        assert "enrichment.item_seasons" not in block
        assert "patt.raid_seasons" not in block
