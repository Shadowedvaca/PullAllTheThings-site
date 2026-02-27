"""feat: screen_permissions — DB-driven Settings nav visibility per rank

Revision ID: 0028
Revises: 0027
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

# Seed data: (screen_key, display_name, url_path, category, category_label,
#              category_order, nav_order, min_rank_level)
SEED = [
    ("my_profile",       "My Profile",       "/profile",                  "player_management", "Player Management", 0, 0, 1),
    ("player_manager",   "Player Manager",   "/admin/players",            "player_management", "Player Management", 0, 1, 4),
    ("users",            "Users",            "/admin/users",              "player_management", "Player Management", 0, 2, 4),
    ("data_quality",     "Data Quality",     "/admin/data-quality",       "player_management", "Player Management", 0, 3, 4),
    ("audit_log",        "Audit Log",        "/admin/audit-log",          "player_management", "Player Management", 0, 4, 4),
    ("raid_tools",       "Raid Tools",       "/admin/raid-tools",         "event_management",  "Event Management",  1, 0, 3),
    ("availability",     "Availability",     "/admin/availability",       "event_management",  "Event Management",  1, 1, 4),
    ("campaigns",        "Campaigns",        "/admin/campaigns",          "campaigns",         "Campaigns",         2, 0, 4),
    ("crafting_sync",    "Crafting Sync",    "/admin/crafting-sync",      "crafting",          "Crafting",          3, 0, 4),
    ("bot_settings",     "Bot Settings",     "/admin/bot-settings",       "settings_admin",    "Settings",          4, 0, 4),
    ("reference_tables", "Reference Tables", "/admin/reference-tables",   "settings_admin",    "Settings",          4, 1, 4),
]


def upgrade() -> None:
    op.create_table(
        "screen_permissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("screen_key", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("url_path", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("category_label", sa.String(100), nullable=False),
        sa.Column("category_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("nav_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("min_rank_level", sa.Integer, nullable=False, server_default="4"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        schema="common",
    )

    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO common.screen_permissions
                (screen_key, display_name, url_path, category, category_label,
                 category_order, nav_order, min_rank_level)
            VALUES
                (:k, :dn, :url, :cat, :cat_label, :cat_ord, :nav_ord, :min_rank)
        """),
        [
            dict(k=k, dn=dn, url=url, cat=cat, cat_label=cat_label,
                 cat_ord=cat_ord, nav_ord=nav_ord, min_rank=min_rank)
            for k, dn, url, cat, cat_label, cat_ord, nav_ord, min_rank in SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("screen_permissions", schema="common")
