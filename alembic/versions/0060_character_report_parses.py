"""Add character_report_parses table and encounter_ids/encounter_map to raid_reports

Revision ID: 0060
Revises: 0059
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add encounter_ids array to raid_reports
    op.add_column(
        "raid_reports",
        sa.Column(
            "encounter_ids",
            ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        schema="guild_identity",
    )

    # 2. Add encounter_map JSONB to raid_reports (encounterID→name lookup)
    op.add_column(
        "raid_reports",
        sa.Column("encounter_map", JSONB(), nullable=True),
        schema="guild_identity",
    )

    # 3. Create character_report_parses table
    op.create_table(
        "character_report_parses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey(
                "guild_identity.wow_characters.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("report_code", sa.String(20), nullable=False),
        sa.Column("encounter_id", sa.Integer(), nullable=False),
        sa.Column("encounter_name", sa.String(100), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("spec", sa.String(50), nullable=True),
        sa.Column("percentile", sa.Numeric(5, 1), nullable=False),
        sa.Column("amount", sa.Numeric(12, 1), nullable=True),
        sa.Column("fight_id", sa.Integer(), nullable=True),
        sa.Column("raid_date", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "character_id",
            "report_code",
            "encounter_id",
            name="uq_crp_char_report_enc",
        ),
        schema="guild_identity",
    )
    op.create_index(
        "idx_crp_character",
        "character_report_parses",
        ["character_id"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_crp_zone",
        "character_report_parses",
        ["zone_id"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_crp_raid_date",
        "character_report_parses",
        ["raid_date"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_index("idx_crp_raid_date", "character_report_parses", schema="guild_identity")
    op.drop_index("idx_crp_zone", "character_report_parses", schema="guild_identity")
    op.drop_index("idx_crp_character", "character_report_parses", schema="guild_identity")
    op.drop_table("character_report_parses", schema="guild_identity")
    op.drop_column("raid_reports", "encounter_map", schema="guild_identity")
    op.drop_column("raid_reports", "encounter_ids", schema="guild_identity")
