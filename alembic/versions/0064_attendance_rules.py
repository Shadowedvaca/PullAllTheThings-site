"""Add attendance_rules table

Revision ID: 0064
Revises: 0063
Create Date: 2026-03-27

Adds patt.attendance_rules table for configurable rule-based attendance
groupings (promotion suggestions, warnings, info groups). Seeds two
built-in promotion rules.
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE patt.attendance_rules (
            id              SERIAL PRIMARY KEY,
            name            VARCHAR(100) NOT NULL,
            group_label     VARCHAR(100) NOT NULL,
            group_type      VARCHAR(20)  NOT NULL CHECK (group_type IN ('promotion', 'warning', 'info')),
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            target_rank_ids INTEGER[] NOT NULL,
            result_rank_id  INTEGER NULL REFERENCES common.guild_ranks(id) ON DELETE SET NULL,
            conditions      JSONB NOT NULL DEFAULT '[]',
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        INSERT INTO patt.attendance_rules
            (name, group_label, group_type, is_active, target_rank_ids, result_rank_id, conditions, sort_order)
        VALUES (
            'Consistent Initiate',
            'Promotion Suggestions',
            'promotion',
            TRUE,
            ARRAY(SELECT id FROM common.guild_ranks WHERE name = 'Initiate'),
            (SELECT id FROM common.guild_ranks WHERE name = 'Member'),
            '[
                {"type": "attendance_pct_in_window", "window_days": 14, "operator": ">=", "value": 100},
                {"type": "min_events_per_week",      "window_days": 14, "operator": ">=", "value": 1}
            ]'::jsonb,
            10
        )
        """
    )

    op.execute(
        """
        INSERT INTO patt.attendance_rules
            (name, group_label, group_type, is_active, target_rank_ids, result_rank_id, conditions, sort_order)
        VALUES (
            'Veteran Attendance',
            'Promotion Suggestions',
            'promotion',
            TRUE,
            ARRAY(SELECT id FROM common.guild_ranks WHERE name = 'Member'),
            (SELECT id FROM common.guild_ranks WHERE name = 'Veteran'),
            '[
                {"type": "attendance_pct_in_window", "window_days": 56, "operator": ">=", "value": 95},
                {"type": "min_events_per_week",      "window_days": 56, "operator": ">=", "value": 1}
            ]'::jsonb,
            20
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patt.attendance_rules")
