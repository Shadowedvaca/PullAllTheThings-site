"""feat: screen permissions cleanup — retire my_gear_plan, add my_characters

Revision ID: 0081
Revises: 0080
Create Date: 2026-04-08

The /gear-plan page has been retired in favour of /my-characters (the unified
character sheet that incorporates the gear plan). This migration:
  - Removes the my_gear_plan screen_permission (url_path=/gear-plan).
  - Adds a my_characters screen_permission (url_path=/my-characters) if it
    does not already exist, so the nav ordering stays tidy.
"""

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'my_gear_plan'")
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('my_characters', 'My Characters', '/my-characters',
             'player_management', 'Player Management', 0, 4, 1)
        ON CONFLICT (screen_key) DO UPDATE
            SET url_path = EXCLUDED.url_path,
                display_name = EXCLUDED.display_name
    """)


def downgrade() -> None:
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'my_characters'")
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('my_gear_plan', 'Gear Plan', '/gear-plan',
             'player_management', 'Player Management', 0, 5, 1)
        ON CONFLICT (screen_key) DO NOTHING
    """)
