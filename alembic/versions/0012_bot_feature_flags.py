"""add feature flag columns to discord_config

Revision ID: 0012
Revises: 0011
Create Date: 2026-02-24

Adds two feature-level toggles to common.discord_config:
  - feature_invite_dm:     gate admin invite-code DMs (OFF by default)
  - feature_onboarding_dm: gate new-member onboarding DMs (OFF by default)

Both default to FALSE so existing deployments are unaffected.
The master bot_dm_enabled must also be TRUE for either to fire.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_config",
        sa.Column("feature_invite_dm", sa.Boolean(), nullable=False, server_default="false"),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column("feature_onboarding_dm", sa.Boolean(), nullable=False, server_default="false"),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("discord_config", "feature_onboarding_dm", schema="common")
    op.drop_column("discord_config", "feature_invite_dm", schema="common")
