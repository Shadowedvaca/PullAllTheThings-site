"""feat: add revocable website authentication sessions

Revision ID: 0182
Revises: 0181
Create Date: 2026-08-09
"""

from alembic import op

revision = "0182"
down_revision = "0181"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE common.auth_sessions (
            id                    VARCHAR(36) PRIMARY KEY,
            user_id               INTEGER NOT NULL
                                  REFERENCES common.users(id) ON DELETE CASCADE,
            rank_level_at_issue   INTEGER NOT NULL,
            issued_at             TIMESTAMPTZ NOT NULL,
            expires_at            TIMESTAMPTZ NOT NULL,
            revoked_at            TIMESTAMPTZ,
            revoked_reason        VARCHAR(50),
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (expires_at > issued_at),
            CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_auth_sessions_user_active
            ON common.auth_sessions (user_id, expires_at)
            WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE FUNCTION common.revoke_sessions_for_user_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.password_hash IS DISTINCT FROM NEW.password_hash THEN
                UPDATE common.auth_sessions
                   SET revoked_at = NOW(), revoked_reason = 'password_change'
                 WHERE user_id = NEW.id AND revoked_at IS NULL;
            ELSIF OLD.is_active IS DISTINCT FROM NEW.is_active AND NOT NEW.is_active THEN
                UPDATE common.auth_sessions
                   SET revoked_at = NOW(), revoked_reason = 'account_deactivated'
                 WHERE user_id = NEW.id AND revoked_at IS NULL;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_users_revoke_auth_sessions
        AFTER UPDATE OF password_hash, is_active ON common.users
        FOR EACH ROW EXECUTE FUNCTION common.revoke_sessions_for_user_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION common.revoke_sessions_for_rank_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.guild_rank_id IS DISTINCT FROM NEW.guild_rank_id
               AND NEW.website_user_id IS NOT NULL THEN
                UPDATE common.auth_sessions
                   SET revoked_at = NOW(), revoked_reason = 'privilege_change'
                 WHERE user_id = NEW.website_user_id AND revoked_at IS NULL;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_players_revoke_auth_sessions
        AFTER UPDATE OF guild_rank_id ON guild_identity.players
        FOR EACH ROW EXECUTE FUNCTION common.revoke_sessions_for_rank_change()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_players_revoke_auth_sessions "
        "ON guild_identity.players"
    )
    op.execute("DROP FUNCTION IF EXISTS common.revoke_sessions_for_rank_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_users_revoke_auth_sessions ON common.users")
    op.execute("DROP FUNCTION IF EXISTS common.revoke_sessions_for_user_change()")
    op.execute("DROP TABLE IF EXISTS common.auth_sessions")
