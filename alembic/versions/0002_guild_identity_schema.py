"""guild_identity schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-21

Creates the guild_identity schema with tables for the Phase 2.5 identity system:
persons, wow_characters, discord_members, identity_links, audit_issues, sync_log
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, TIMESTAMP

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS guild_identity")

    # -------------------------------------------------------------------------
    # persons
    # -------------------------------------------------------------------------
    op.create_table(
        "persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # wow_characters
    # -------------------------------------------------------------------------
    op.create_table(
        "wow_characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.persons.id", ondelete="SET NULL"),
        ),
        # From Blizzard API
        sa.Column("character_name", sa.String(50), nullable=False),
        sa.Column("realm_slug", sa.String(50), nullable=False),
        sa.Column("realm_name", sa.String(100)),
        sa.Column("character_class", sa.String(30)),
        sa.Column("active_spec", sa.String(50)),
        sa.Column("level", sa.Integer()),
        sa.Column("item_level", sa.Integer()),
        sa.Column("guild_rank", sa.Integer()),
        sa.Column("guild_rank_name", sa.String(50)),
        sa.Column("last_login_timestamp", sa.BigInteger()),
        # From addon
        sa.Column("guild_note", sa.Text()),
        sa.Column("officer_note", sa.Text()),
        sa.Column("addon_last_seen", TIMESTAMP(timezone=True)),
        # Metadata
        sa.Column("is_main", sa.Boolean(), server_default="false"),
        sa.Column("role_category", sa.String(10)),
        sa.Column("blizzard_last_sync", TIMESTAMP(timezone=True)),
        sa.Column("addon_last_sync", TIMESTAMP(timezone=True)),
        sa.Column("first_seen", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("removed_at", TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("character_name", "realm_slug"),
        schema="guild_identity",
    )

    op.create_index(
        "idx_wow_chars_person",
        "wow_characters",
        ["person_id"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_wow_chars_rank",
        "wow_characters",
        ["guild_rank"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_wow_chars_removed",
        "wow_characters",
        ["removed_at"],
        schema="guild_identity",
    )
    # Functional index on lowercase name — must use raw SQL
    op.execute(
        "CREATE INDEX idx_wow_chars_name_lower "
        "ON guild_identity.wow_characters (LOWER(character_name))"
    )

    # -------------------------------------------------------------------------
    # discord_members
    # -------------------------------------------------------------------------
    op.create_table(
        "discord_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.persons.id", ondelete="SET NULL"),
        ),
        sa.Column("discord_id", sa.String(25), nullable=False, unique=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(50)),
        sa.Column("highest_guild_role", sa.String(30)),
        sa.Column("all_guild_roles", ARRAY(sa.Text())),
        sa.Column("joined_server_at", TIMESTAMP(timezone=True)),
        sa.Column("last_sync", TIMESTAMP(timezone=True)),
        sa.Column("is_present", sa.Boolean(), server_default="true"),
        sa.Column("removed_at", TIMESTAMP(timezone=True)),
        sa.Column("first_seen", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="guild_identity",
    )

    op.create_index(
        "idx_discord_members_person",
        "discord_members",
        ["person_id"],
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_discord_members_username "
        "ON guild_identity.discord_members (LOWER(username))"
    )
    op.execute(
        "CREATE INDEX idx_discord_members_display "
        "ON guild_identity.discord_members (LOWER(display_name))"
    )

    # -------------------------------------------------------------------------
    # identity_links
    # -------------------------------------------------------------------------
    op.create_table(
        "identity_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wow_character_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            unique=True,
        ),
        sa.Column(
            "discord_member_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.discord_members.id", ondelete="CASCADE"),
            unique=True,
        ),
        sa.Column("link_source", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False, server_default="high"),
        sa.Column("is_confirmed", sa.Boolean(), server_default="false"),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("confirmed_at", TIMESTAMP(timezone=True)),
        sa.Column("confirmed_by", sa.String(50)),
        sa.CheckConstraint(
            "wow_character_id IS NOT NULL OR discord_member_id IS NOT NULL",
            name="identity_links_target_not_null",
        ),
        schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # audit_issues
    # -------------------------------------------------------------------------
    op.create_table(
        "audit_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
        sa.Column(
            "wow_character_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "discord_member_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.discord_members.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.persons.id", ondelete="SET NULL"),
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", JSONB()),
        sa.Column("first_detected", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("last_detected", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("notified_at", TIMESTAMP(timezone=True)),
        sa.Column("resolved_at", TIMESTAMP(timezone=True)),
        sa.Column("resolved_by", sa.String(50)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("issue_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("issue_hash", "resolved_at", name="uq_audit_issue_hash_resolved"),
        schema="guild_identity",
    )

    op.create_index(
        "idx_audit_issues_type",
        "audit_issues",
        ["issue_type"],
        schema="guild_identity",
    )
    # Partial indexes must use raw SQL
    op.execute(
        "CREATE INDEX idx_audit_issues_unresolved "
        "ON guild_identity.audit_issues (resolved_at) "
        "WHERE resolved_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_audit_issues_unnotified "
        "ON guild_identity.audit_issues (notified_at) "
        "WHERE notified_at IS NULL AND resolved_at IS NULL"
    )

    # -------------------------------------------------------------------------
    # sync_log
    # -------------------------------------------------------------------------
    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("characters_found", sa.Integer()),
        sa.Column("characters_updated", sa.Integer()),
        sa.Column("characters_new", sa.Integer()),
        sa.Column("characters_removed", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("started_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", TIMESTAMP(timezone=True)),
        schema="guild_identity",
    )

    op.execute(
        "CREATE INDEX idx_sync_log_source "
        "ON guild_identity.sync_log (source, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_sync_log_source")
    op.drop_table("sync_log", schema="guild_identity")

    op.execute("DROP INDEX IF EXISTS guild_identity.idx_audit_issues_unnotified")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_audit_issues_unresolved")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_audit_issues_type")
    op.drop_table("audit_issues", schema="guild_identity")

    op.drop_table("identity_links", schema="guild_identity")

    op.execute("DROP INDEX IF EXISTS guild_identity.idx_discord_members_display")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_discord_members_username")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_discord_members_person")
    op.drop_table("discord_members", schema="guild_identity")

    op.execute("DROP INDEX IF EXISTS guild_identity.idx_wow_chars_name_lower")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_wow_chars_removed")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_wow_chars_rank")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_wow_chars_person")
    op.drop_table("wow_characters", schema="guild_identity")

    op.drop_table("persons", schema="guild_identity")

    op.execute("DROP SCHEMA IF EXISTS guild_identity")
