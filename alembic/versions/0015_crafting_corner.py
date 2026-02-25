"""crafting corner tables and player notification preference

Revision ID: 0015
Revises: 0014
Create Date: 2026-02-25

Phase 2.8 — Crafting Corner
- guild_identity.professions
- guild_identity.profession_tiers
- guild_identity.recipes (with generated wowhead_url)
- guild_identity.character_recipes
- guild_identity.crafting_sync_config (seeded with one row)
- guild_identity.players.crafting_notifications_enabled column
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Professions reference table
    op.create_table(
        "professions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("blizzard_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blizzard_id"),
        sa.UniqueConstraint("name"),
        schema="guild_identity",
    )

    # Profession tiers (expansion-specific)
    op.create_table(
        "profession_tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profession_id", sa.Integer(), nullable=False),
        sa.Column("blizzard_tier_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("expansion_name", sa.String(50), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.ForeignKeyConstraint(
            ["profession_id"], ["guild_identity.professions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blizzard_tier_id"),
        sa.UniqueConstraint("profession_id", "blizzard_tier_id", name="uq_prof_tier"),
        schema="guild_identity",
    )

    # Recipes reference table
    # NOTE: PostgreSQL supports GENERATED ALWAYS AS ... STORED for computed columns.
    # We use server_default approach via raw SQL for the generated column.
    op.execute(
        """
        CREATE TABLE guild_identity.recipes (
            id SERIAL PRIMARY KEY,
            blizzard_spell_id INTEGER NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            profession_id INTEGER NOT NULL REFERENCES guild_identity.professions(id) ON DELETE CASCADE,
            tier_id INTEGER NOT NULL REFERENCES guild_identity.profession_tiers(id) ON DELETE CASCADE,
            wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
                'https://www.wowhead.com/spell=' || blizzard_spell_id
            ) STORED,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.create_index("idx_recipes_profession", "recipes", ["profession_id"], schema="guild_identity")
    op.create_index("idx_recipes_tier", "recipes", ["tier_id"], schema="guild_identity")
    op.execute(
        "CREATE INDEX idx_recipes_name_lower ON guild_identity.recipes(LOWER(name))"
    )

    # Character ↔ Recipe junction
    op.create_table(
        "character_recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.ForeignKeyConstraint(
            ["character_id"], ["guild_identity.wow_characters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["guild_identity.recipes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id", "recipe_id", name="uq_char_recipe"),
        schema="guild_identity",
    )
    op.create_index("idx_char_recipes_character", "character_recipes", ["character_id"], schema="guild_identity")
    op.create_index("idx_char_recipes_recipe", "character_recipes", ["recipe_id"], schema="guild_identity")

    # Crafting sync config (single-row table)
    op.create_table(
        "crafting_sync_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("current_cadence", sa.String(10), server_default="weekly", nullable=False),
        sa.Column("cadence_override_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expansion_name", sa.String(50), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("season_start_date", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_first_season", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_sync_duration_seconds", sa.Float(), nullable=True),
        sa.Column("last_sync_characters_processed", sa.Integer(), nullable=True),
        sa.Column("last_sync_recipes_found", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="guild_identity",
    )
    # Seed the single config row
    op.execute(
        "INSERT INTO guild_identity.crafting_sync_config (current_cadence) VALUES ('weekly')"
    )

    # Add crafting notification preference to players
    op.add_column(
        "players",
        sa.Column(
            "crafting_notifications_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("players", "crafting_notifications_enabled", schema="guild_identity")
    op.drop_table("crafting_sync_config", schema="guild_identity")
    op.drop_index("idx_char_recipes_recipe", table_name="character_recipes", schema="guild_identity")
    op.drop_index("idx_char_recipes_character", table_name="character_recipes", schema="guild_identity")
    op.drop_table("character_recipes", schema="guild_identity")
    op.execute("DROP INDEX IF EXISTS idx_recipes_name_lower")
    op.drop_index("idx_recipes_tier", table_name="recipes", schema="guild_identity")
    op.drop_index("idx_recipes_profession", table_name="recipes", schema="guild_identity")
    op.drop_table("recipes", schema="guild_identity")
    op.drop_table("profession_tiers", schema="guild_identity")
    op.drop_table("professions", schema="guild_identity")
