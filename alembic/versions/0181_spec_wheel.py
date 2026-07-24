"""feat: seasonal main and off-spec wheel history

Revision ID: 0181
Revises: 0180
Create Date: 2026-07-23
"""

from alembic import op

revision = "0181"
down_revision = "0180"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE patt.spec_wheel_rolls (
            id                SERIAL PRIMARY KEY,
            player_id         INTEGER NOT NULL
                              REFERENCES guild_identity.players(id) ON DELETE CASCADE,
            season_id         INTEGER NOT NULL
                              REFERENCES patt.raid_seasons(id) ON DELETE CASCADE,
            slot              VARCHAR(10) NOT NULL
                              CHECK (slot IN ('main', 'offspec')),
            first_spec_id     INTEGER NOT NULL
                              REFERENCES ref.specializations(id),
            first_rolled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            latest_spec_id    INTEGER NOT NULL
                              REFERENCES ref.specializations(id),
            latest_rolled_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            roll_count        INTEGER NOT NULL DEFAULT 1 CHECK (roll_count >= 1),
            UNIQUE (player_id, season_id, slot)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_spec_wheel_rolls_player_season
            ON patt.spec_wheel_rolls (player_id, season_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patt.spec_wheel_rolls")
