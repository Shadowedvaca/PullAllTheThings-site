"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure schemas exist
    op.execute("CREATE SCHEMA IF NOT EXISTS common")
    op.execute("CREATE SCHEMA IF NOT EXISTS patt")

    # ---------------------------------------------------------------------------
    # common schema
    # ---------------------------------------------------------------------------

    op.create_table(
        "guild_ranks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("level", sa.Integer(), nullable=False, unique=True),
        sa.Column("discord_role_id", sa.String(20)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True),
        sa.Column("phone", sa.String(20)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    op.create_table(
        "guild_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("common.users.id"), unique=True),
        sa.Column("discord_id", sa.String(20), unique=True),
        sa.Column("discord_username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100)),
        sa.Column(
            "rank_id",
            sa.Integer(),
            sa.ForeignKey("common.guild_ranks.id"),
            nullable=False,
        ),
        sa.Column("rank_source", sa.String(20), server_default="manual"),
        sa.Column("registered_at", TIMESTAMP(timezone=True)),
        sa.Column("last_seen_at", TIMESTAMP(timezone=True)),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    op.create_table(
        "characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "member_id",
            sa.Integer(),
            sa.ForeignKey("common.guild_members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("realm", sa.String(50), nullable=False),
        sa.Column("class", sa.String(30), nullable=False),
        sa.Column("spec", sa.String(30)),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("main_alt", sa.String(10), server_default="main"),
        sa.Column("armory_url", sa.Text()),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", "realm"),
        schema="common",
    )

    op.create_table(
        "discord_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_discord_id", sa.String(20), nullable=False),
        sa.Column("role_sync_interval_hours", sa.Integer(), server_default="24"),
        sa.Column("default_announcement_channel_id", sa.String(20)),
        sa.Column("last_role_sync_at", TIMESTAMP(timezone=True)),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("common.guild_members.id")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("common.guild_members.id")),
        sa.Column("used_at", TIMESTAMP(timezone=True)),
        sa.Column("expires_at", TIMESTAMP(timezone=True)),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    # ---------------------------------------------------------------------------
    # patt schema
    # ---------------------------------------------------------------------------

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("type", sa.String(20), server_default="ranked_choice"),
        sa.Column("picks_per_voter", sa.Integer(), server_default="3"),
        sa.Column("min_rank_to_vote", sa.Integer(), nullable=False),
        sa.Column("min_rank_to_view", sa.Integer()),
        sa.Column("start_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("early_close_if_all_voted", sa.Boolean(), server_default="true"),
        sa.Column("discord_channel_id", sa.String(20)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("common.guild_members.id")),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )

    op.create_table(
        "campaign_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column(
            "associated_member_id", sa.Integer(), sa.ForeignKey("common.guild_members.id")
        ),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )

    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.Integer(),
            sa.ForeignKey("common.guild_members.id"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaign_entries.id"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("voted_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("campaign_id", "member_id", "rank"),
        schema="patt",
    )

    op.create_table(
        "campaign_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaign_entries.id"),
            nullable=False,
        ),
        sa.Column("first_place_count", sa.Integer(), server_default="0"),
        sa.Column("second_place_count", sa.Integer(), server_default="0"),
        sa.Column("third_place_count", sa.Integer(), server_default="0"),
        sa.Column("weighted_score", sa.Integer(), server_default="0"),
        sa.Column("final_rank", sa.Integer()),
        sa.Column("calculated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )

    op.create_table(
        "contest_agent_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("patt.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("discord_message_id", sa.String(20)),
        sa.Column("posted_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_table("contest_agent_log", schema="patt")
    op.drop_table("campaign_results", schema="patt")
    op.drop_table("votes", schema="patt")
    op.drop_table("campaign_entries", schema="patt")
    op.drop_table("campaigns", schema="patt")
    op.drop_table("invite_codes", schema="common")
    op.drop_table("discord_config", schema="common")
    op.drop_table("characters", schema="common")
    op.drop_table("guild_members", schema="common")
    op.drop_table("users", schema="common")
    op.drop_table("guild_ranks", schema="common")
