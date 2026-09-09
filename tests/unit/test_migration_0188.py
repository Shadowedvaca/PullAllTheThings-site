"""Static contracts for migration 0188 Catalyst goals and season-safe tier sources."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "0188_gear_plan_catalyst_routes.py"
).read_text(encoding="utf-8")


def test_migration_chain_and_goal_route_columns() -> None:
    assert 'revision = "0188"' in MIGRATION
    assert 'down_revision = "0187"' in MIGRATION
    assert "recommendation_type VARCHAR(10)" in MIGRATION
    assert "catalyst_base_item_id INTEGER" in MIGRATION
    assert "catalyst_base_item_name VARCHAR(200)" in MIGRATION


def test_tier_sources_are_restricted_to_active_season_raids() -> None:
    assert "tier_rs.is_active = TRUE" in MIGRATION
    assert "token_season.season_id = tier_season.season_id" in MIGRATION
    assert "es.blizzard_instance_id = ANY(tier_rs.current_raid_ids)" in MIGRATION


def test_downgrade_removes_goal_route_columns() -> None:
    assert "DROP COLUMN IF EXISTS catalyst_base_item_name" in MIGRATION
    assert "DROP COLUMN IF EXISTS catalyst_base_item_id" in MIGRATION
    assert "DROP COLUMN IF EXISTS recommendation_type" in MIGRATION
