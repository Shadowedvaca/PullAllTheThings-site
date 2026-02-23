"""phase 2.6: onboarding sessions table + invite_codes columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- guild_identity.onboarding_sessions --------------------------------
    op.create_table(
        "onboarding_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "discord_member_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.discord_members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("discord_id", sa.String(25), nullable=False),

        # Conversation state
        sa.Column("state", sa.String(30), nullable=False, server_default="awaiting_dm"),

        # Self-reported data
        sa.Column("reported_main_name", sa.String(50), nullable=True),
        sa.Column("reported_main_realm", sa.String(100), nullable=True),
        sa.Column("reported_alt_names", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("is_in_guild", sa.Boolean(), nullable=True),

        # Verification
        sa.Column("verification_attempts", sa.Integer(), server_default="0"),
        sa.Column("last_verification_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "verified_person_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.persons.id"),
            nullable=True,
        ),

        # Provisioning tracking
        sa.Column("website_invite_sent", sa.Boolean(), server_default="false"),
        sa.Column("website_invite_code", sa.String(50), nullable=True),
        sa.Column("roster_entries_created", sa.Boolean(), server_default="false"),
        sa.Column("discord_role_assigned", sa.Boolean(), server_default="false"),

        # Lifecycle timestamps
        sa.Column("dm_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dm_completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),

        sa.UniqueConstraint("discord_id", name="uq_onboarding_discord_id"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_onboarding_state",
        "onboarding_sessions",
        ["state"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_onboarding_deadline",
        "onboarding_sessions",
        ["deadline_at"],
        schema="guild_identity",
        postgresql_where=sa.text("state = 'pending_verification'"),
    )

    # -- common.invite_codes additions -------------------------------------
    op.add_column(
        "invite_codes",
        sa.Column(
            "generated_by",
            sa.String(30),
            nullable=False,
            server_default="manual",
        ),
        schema="common",
    )
    op.add_column(
        "invite_codes",
        sa.Column(
            "onboarding_session_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.onboarding_sessions.id"),
            nullable=True,
        ),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("invite_codes", "onboarding_session_id", schema="common")
    op.drop_column("invite_codes", "generated_by", schema="common")
    op.drop_index("idx_onboarding_deadline", "onboarding_sessions", schema="guild_identity")
    op.drop_index("idx_onboarding_state", "onboarding_sessions", schema="guild_identity")
    op.drop_table("onboarding_sessions", schema="guild_identity")
