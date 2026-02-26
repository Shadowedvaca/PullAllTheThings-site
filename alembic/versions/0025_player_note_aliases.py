"""feat: player_note_aliases table — persistent alias registry for note-key → player mapping

Revision ID: 0025
Revises: 0024
Create Date: 2026-02-25
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE guild_identity.player_note_aliases (
            id         SERIAL PRIMARY KEY,
            player_id  INTEGER NOT NULL
                           REFERENCES guild_identity.players(id) ON DELETE CASCADE,
            alias      VARCHAR(50) NOT NULL,
            source     VARCHAR(30) NOT NULL DEFAULT 'note_match',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(player_id, alias)
        )
    """)

    # Backfill from existing player_characters using the same
    # _extract_note_key logic (approximated in SQL):
    #   - take first space-separated word
    #   - take first hyphen-separated part
    #   - strip trailing 's possessive
    #   - strip punctuation
    #   - lower-case
    #   - strip trailing 's' from words longer than 3 chars
    op.execute("""
        INSERT INTO guild_identity.player_note_aliases (player_id, alias, source)
        SELECT DISTINCT pc.player_id,
               LOWER(
                   REGEXP_REPLACE(
                       REGEXP_REPLACE(
                           SPLIT_PART(SPLIT_PART(wc.guild_note, ' ', 1), '-', 1),
                           '''s$', '', 'i'
                       ),
                       '[''.,;:!?()]', '', 'g'
                   )
               ) AS alias,
               'backfill'
        FROM guild_identity.player_characters pc
        JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
        WHERE wc.removed_at IS NULL
          AND wc.guild_note IS NOT NULL
          AND wc.guild_note != ''
          AND LENGTH(
                  LOWER(
                      REGEXP_REPLACE(
                          REGEXP_REPLACE(
                              SPLIT_PART(SPLIT_PART(wc.guild_note, ' ', 1), '-', 1),
                              '''s$', '', 'i'
                          ),
                          '[''.,;:!?()]', '', 'g'
                      )
                  )
              ) >= 2
        ON CONFLICT (player_id, alias) DO NOTHING
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS guild_identity.player_note_aliases")
