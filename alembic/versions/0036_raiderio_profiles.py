"""Phase 4.4: Raider.IO integration — raiderio_profiles table

Revision ID: 0036
Revises: 0035
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "raiderio_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season", sa.String(30), nullable=False),
        sa.Column("overall_score", sa.Numeric(7, 1), server_default="0"),
        sa.Column("dps_score", sa.Numeric(7, 1), server_default="0"),
        sa.Column("healer_score", sa.Numeric(7, 1), server_default="0"),
        sa.Column("tank_score", sa.Numeric(7, 1), server_default="0"),
        sa.Column("score_color", sa.String(7)),
        sa.Column("raid_progression", sa.String(100)),
        sa.Column("best_runs", JSONB, server_default="'[]'::jsonb"),
        sa.Column("recent_runs", JSONB, server_default="'[]'::jsonb"),
        sa.Column("profile_url", sa.String(255)),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("character_id", "season", name="uq_rio_char_season"),
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_rio_char ON guild_identity.raiderio_profiles (character_id)"
    )
    op.execute(
        "CREATE INDEX idx_rio_season ON guild_identity.raiderio_profiles (season)"
    )
    op.execute(
        "CREATE INDEX idx_rio_score ON guild_identity.raiderio_profiles (overall_score DESC)"
    )


def downgrade():
    op.drop_table("raiderio_profiles", schema="guild_identity")
