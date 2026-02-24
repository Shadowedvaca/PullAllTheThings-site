"""phase 2.7: data model migration — clean 3NF rebuild

Revision ID: 0007
Revises: 0006
Create Date: 2026-02-23

WARNING: This migration is not safely reversible.
Take a database backup before running: pg_dump patt_db > patt_db_backup_pre_2.7.sql

Changes:
1. Create reference tables: guild_identity.roles, classes, specializations (seeded)
2. Rename guild_identity.persons → players, add new columns
3. Rename guild_identity.discord_members → discord_users, drop person_id
4. Restructure guild_identity.wow_characters: add FK columns, drop text columns
5. Create guild_identity.player_characters bridge table
6. Repoint FKs: invite_codes, member_availability, campaigns, campaign_entries, votes
7. Update guild_identity.onboarding_sessions references
8. Drop dead tables: identity_links, common.characters, common.guild_members
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # Step 1: Create reference tables
    # -------------------------------------------------------------------------

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(20), nullable=False, unique=True),
        schema="guild_identity",
    )
    conn.execute(sa.text("""
        INSERT INTO guild_identity.roles (name) VALUES
            ('Tank'), ('Healer'), ('Melee DPS'), ('Ranged DPS')
    """))

    op.create_table(
        "classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(30), nullable=False, unique=True),
        sa.Column("color_hex", sa.String(7), nullable=True),
        schema="guild_identity",
    )
    conn.execute(sa.text("""
        INSERT INTO guild_identity.classes (name, color_hex) VALUES
            ('Death Knight', '#C41E3A'),
            ('Demon Hunter', '#A330C9'),
            ('Druid', '#FF7C0A'),
            ('Evoker', '#33937F'),
            ('Hunter', '#AAD372'),
            ('Mage', '#3FC7EB'),
            ('Monk', '#00FF98'),
            ('Paladin', '#F48CBA'),
            ('Priest', '#FFFFFF'),
            ('Rogue', '#FFF468'),
            ('Shaman', '#0070DD'),
            ('Warlock', '#8788EE'),
            ('Warrior', '#C69B6D')
    """))

    op.create_table(
        "specializations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "class_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.classes.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column(
            "default_role_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.roles.id"),
            nullable=False,
        ),
        sa.Column("wowhead_slug", sa.String(50), nullable=True),
        sa.UniqueConstraint("class_id", "name", name="uq_specializations_class_name"),
        schema="guild_identity",
    )
    # Seed all specs. Uses subselects to avoid hardcoding IDs.
    conn.execute(sa.text("""
        INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug)
        SELECT c.id, s.name, r.id, s.slug FROM (VALUES
            ('Death Knight','Blood','Tank','blood-death-knight'),
            ('Death Knight','Frost','Melee DPS','frost-death-knight'),
            ('Death Knight','Unholy','Melee DPS','unholy-death-knight'),
            ('Demon Hunter','Havoc','Melee DPS','havoc-demon-hunter'),
            ('Demon Hunter','Vengeance','Tank','vengeance-demon-hunter'),
            ('Druid','Balance','Ranged DPS','balance-druid'),
            ('Druid','Feral','Melee DPS','feral-druid'),
            ('Druid','Guardian','Tank','guardian-druid'),
            ('Druid','Restoration','Healer','restoration-druid'),
            ('Evoker','Devastation','Ranged DPS','devastation-evoker'),
            ('Evoker','Preservation','Healer','preservation-evoker'),
            ('Evoker','Augmentation','Ranged DPS','augmentation-evoker'),
            ('Hunter','Beast Mastery','Ranged DPS','beast-mastery-hunter'),
            ('Hunter','Marksmanship','Ranged DPS','marksmanship-hunter'),
            ('Hunter','Survival','Melee DPS','survival-hunter'),
            ('Mage','Arcane','Ranged DPS','arcane-mage'),
            ('Mage','Fire','Ranged DPS','fire-mage'),
            ('Mage','Frost','Ranged DPS','frost-mage'),
            ('Monk','Brewmaster','Tank','brewmaster-monk'),
            ('Monk','Mistweaver','Healer','mistweaver-monk'),
            ('Monk','Windwalker','Melee DPS','windwalker-monk'),
            ('Paladin','Holy','Healer','holy-paladin'),
            ('Paladin','Protection','Tank','protection-paladin'),
            ('Paladin','Retribution','Melee DPS','retribution-paladin'),
            ('Priest','Discipline','Healer','discipline-priest'),
            ('Priest','Holy','Healer','holy-priest'),
            ('Priest','Shadow','Ranged DPS','shadow-priest'),
            ('Rogue','Assassination','Melee DPS','assassination-rogue'),
            ('Rogue','Outlaw','Melee DPS','outlaw-rogue'),
            ('Rogue','Subtlety','Melee DPS','subtlety-rogue'),
            ('Shaman','Elemental','Ranged DPS','elemental-shaman'),
            ('Shaman','Enhancement','Melee DPS','enhancement-shaman'),
            ('Shaman','Restoration','Healer','restoration-shaman'),
            ('Warlock','Affliction','Ranged DPS','affliction-warlock'),
            ('Warlock','Demonology','Ranged DPS','demonology-warlock'),
            ('Warlock','Destruction','Ranged DPS','destruction-warlock'),
            ('Warrior','Arms','Melee DPS','arms-warrior'),
            ('Warrior','Fury','Melee DPS','fury-warrior'),
            ('Warrior','Protection','Tank','protection-warrior')
        ) AS s(class_name, name, role_name, slug)
        JOIN guild_identity.classes c ON c.name = s.class_name
        JOIN guild_identity.roles r ON r.name = s.role_name
    """))

    # -------------------------------------------------------------------------
    # Step 2: Rename persons → players, add new columns
    # -------------------------------------------------------------------------

    op.rename_table("persons", "players", schema="guild_identity")

    op.add_column(
        "players",
        sa.Column(
            "discord_user_id",
            sa.Integer(),
            nullable=True,
        ),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("website_user_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("guild_rank_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("guild_rank_source", sa.String(20), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("main_character_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("main_spec_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("offspec_character_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column("offspec_spec_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )

    # Add FK constraints after columns exist
    op.create_foreign_key(
        "fk_players_discord_user",
        "players", "discord_users",
        ["discord_user_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_unique_constraint(
        "uq_players_discord_user_id", "players", ["discord_user_id"],
        schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_players_website_user",
        "players", "users",
        ["website_user_id"], ["id"],
        source_schema="guild_identity", referent_schema="common",
    )
    op.create_unique_constraint(
        "uq_players_website_user_id", "players", ["website_user_id"],
        schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_players_guild_rank",
        "players", "guild_ranks",
        ["guild_rank_id"], ["id"],
        source_schema="guild_identity", referent_schema="common",
    )
    op.create_foreign_key(
        "fk_players_main_character",
        "players", "wow_characters",
        ["main_character_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_players_main_spec",
        "players", "specializations",
        ["main_spec_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_players_offspec_character",
        "players", "wow_characters",
        ["offspec_character_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_players_offspec_spec",
        "players", "specializations",
        ["offspec_spec_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 3: Rename discord_members → discord_users, drop person_id
    # -------------------------------------------------------------------------

    op.rename_table("discord_members", "discord_users", schema="guild_identity")

    # Drop the person_id column (relationship moved to players.discord_user_id)
    op.drop_column("discord_users", "person_id", schema="guild_identity")

    # -------------------------------------------------------------------------
    # Step 4: Restructure wow_characters
    # -------------------------------------------------------------------------

    # Drop old person_id, is_main, role_category from wow_characters
    # Drop FK constraint on person_id first
    op.drop_constraint(
        "wow_characters_person_id_fkey", "wow_characters", schema="guild_identity", type_="foreignkey"
    )
    op.drop_column("wow_characters", "person_id", schema="guild_identity")
    op.drop_column("wow_characters", "is_main", schema="guild_identity")
    op.drop_column("wow_characters", "role_category", schema="guild_identity")

    # Add new FK columns
    op.add_column(
        "wow_characters",
        sa.Column("class_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "wow_characters",
        sa.Column("active_spec_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "wow_characters",
        sa.Column("guild_rank_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )

    # Populate class_id from character_class text
    conn.execute(sa.text("""
        UPDATE guild_identity.wow_characters wc
        SET class_id = c.id
        FROM guild_identity.classes c
        WHERE LOWER(wc.character_class) = LOWER(c.name)
    """))

    # Populate active_spec_id from (class_id, active_spec text)
    conn.execute(sa.text("""
        UPDATE guild_identity.wow_characters wc
        SET active_spec_id = s.id
        FROM guild_identity.specializations s
        WHERE wc.class_id = s.class_id
          AND LOWER(wc.active_spec) = LOWER(s.name)
    """))

    # Populate guild_rank_id from guild_rank integer (level) → common.guild_ranks.id
    conn.execute(sa.text("""
        UPDATE guild_identity.wow_characters wc
        SET guild_rank_id = gr.id
        FROM common.guild_ranks gr
        WHERE wc.guild_rank = gr.level
    """))

    # Add FK constraints on new columns
    op.create_foreign_key(
        "fk_wow_characters_class",
        "wow_characters", "classes",
        ["class_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_wow_characters_active_spec",
        "wow_characters", "specializations",
        ["active_spec_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_wow_characters_guild_rank",
        "wow_characters", "guild_ranks",
        ["guild_rank_id"], ["id"],
        source_schema="guild_identity", referent_schema="common",
    )

    # Drop old text columns
    op.drop_column("wow_characters", "character_class", schema="guild_identity")
    op.drop_column("wow_characters", "active_spec", schema="guild_identity")
    op.drop_column("wow_characters", "guild_rank", schema="guild_identity")
    op.drop_column("wow_characters", "guild_rank_name", schema="guild_identity")

    # -------------------------------------------------------------------------
    # Step 5: Create player_characters bridge table
    # -------------------------------------------------------------------------

    op.create_table(
        "player_characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("player_id", "character_id", name="uq_player_characters"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_player_characters_player",
        "player_characters",
        ["player_id"],
        schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 6: Repoint FKs — common.invite_codes
    # -------------------------------------------------------------------------

    # Add new player_id and created_by_player_id columns
    op.add_column(
        "invite_codes",
        sa.Column("player_id", sa.Integer(), nullable=True),
        schema="common",
    )
    op.add_column(
        "invite_codes",
        sa.Column("created_by_player_id", sa.Integer(), nullable=True),
        schema="common",
    )

    # Populate player_id from member_id via guild_members mapping (will be done
    # by data migration script after this migration runs; for now migrate existing
    # invite codes that reference guild_members by finding matching players via
    # discord_id linkage — skip if no match since invite_codes.member_id is nullable)
    # NOTE: The actual data linkage is done in scripts/migrate_to_players.py

    # Drop old FK and columns
    op.drop_constraint(
        "invite_codes_member_id_fkey", "invite_codes", schema="common", type_="foreignkey"
    )
    op.drop_constraint(
        "invite_codes_created_by_fkey", "invite_codes", schema="common", type_="foreignkey"
    )
    op.drop_column("invite_codes", "member_id", schema="common")
    op.drop_column("invite_codes", "created_by", schema="common")

    # Add new FK constraints
    op.create_foreign_key(
        "fk_invite_codes_player",
        "invite_codes", "players",
        ["player_id"], ["id"],
        source_schema="common", referent_schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_invite_codes_created_by_player",
        "invite_codes", "players",
        ["created_by_player_id"], ["id"],
        source_schema="common", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 7: Repoint FKs — common.member_availability
    # -------------------------------------------------------------------------

    # Drop old constraint
    op.drop_constraint(
        "member_availability_member_id_day_of_week_key",
        "member_availability",
        schema="common",
        type_="unique",
    )
    op.drop_constraint(
        "member_availability_member_id_fkey",
        "member_availability",
        schema="common",
        type_="foreignkey",
    )

    # Rename column
    op.alter_column(
        "member_availability", "member_id", new_column_name="player_id", schema="common"
    )

    # Re-add constraints
    op.create_unique_constraint(
        "member_availability_player_id_day_of_week_key",
        "member_availability",
        ["player_id", "day_of_week"],
        schema="common",
    )
    op.create_foreign_key(
        "fk_member_availability_player",
        "member_availability", "players",
        ["player_id"], ["id"],
        source_schema="common", referent_schema="guild_identity",
        ondelete="CASCADE",
    )

    # -------------------------------------------------------------------------
    # Step 8: Repoint FKs — patt.campaigns
    # -------------------------------------------------------------------------

    op.add_column(
        "campaigns",
        sa.Column("created_by_player_id", sa.Integer(), nullable=True),
        schema="patt",
    )

    # Migrate data: created_by (guild_members.id) → created_by_player_id
    # This will be done by the data migration script.

    op.drop_constraint(
        "campaigns_created_by_fkey", "campaigns", schema="patt", type_="foreignkey"
    )
    op.drop_column("campaigns", "created_by", schema="patt")

    op.create_foreign_key(
        "fk_campaigns_created_by_player",
        "campaigns", "players",
        ["created_by_player_id"], ["id"],
        source_schema="patt", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 9: Repoint FKs — patt.campaign_entries
    # -------------------------------------------------------------------------

    op.add_column(
        "campaign_entries",
        sa.Column("player_id", sa.Integer(), nullable=True),
        schema="patt",
    )

    op.drop_constraint(
        "campaign_entries_associated_member_id_fkey",
        "campaign_entries",
        schema="patt",
        type_="foreignkey",
    )
    op.drop_column("campaign_entries", "associated_member_id", schema="patt")

    op.create_foreign_key(
        "fk_campaign_entries_player",
        "campaign_entries", "players",
        ["player_id"], ["id"],
        source_schema="patt", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 10: Repoint FKs — patt.votes
    # -------------------------------------------------------------------------

    # Drop unique constraint that includes member_id
    op.drop_constraint(
        "votes_campaign_id_member_id_rank_key", "votes", schema="patt", type_="unique"
    )
    op.add_column(
        "votes",
        sa.Column("player_id", sa.Integer(), nullable=True),
        schema="patt",
    )

    # Copy member_id → player_id (will be populated by data migration script;
    # for now NULL since guild_members.id != players.id until migration runs)

    op.drop_constraint(
        "votes_member_id_fkey", "votes", schema="patt", type_="foreignkey"
    )
    op.drop_column("votes", "member_id", schema="patt")

    # Make player_id NOT NULL (safe since votes table was empty / no active votes)
    op.alter_column("votes", "player_id", nullable=False, schema="patt")

    op.create_unique_constraint(
        "votes_campaign_id_player_id_rank_key",
        "votes",
        ["campaign_id", "player_id", "rank"],
        schema="patt",
    )
    op.create_foreign_key(
        "fk_votes_player",
        "votes", "players",
        ["player_id"], ["id"],
        source_schema="patt", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 11: Update onboarding_sessions references
    # -------------------------------------------------------------------------

    # Update discord_member_id FK target (table was renamed)
    op.drop_constraint(
        "onboarding_sessions_discord_member_id_fkey",
        "onboarding_sessions",
        schema="guild_identity",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_onboarding_discord_user",
        "onboarding_sessions", "discord_users",
        ["discord_member_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
        ondelete="CASCADE",
    )

    # Rename verified_person_id → verified_player_id
    op.drop_constraint(
        "onboarding_sessions_verified_person_id_fkey",
        "onboarding_sessions",
        schema="guild_identity",
        type_="foreignkey",
    )
    op.alter_column(
        "onboarding_sessions",
        "verified_person_id",
        new_column_name="verified_player_id",
        schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_onboarding_verified_player",
        "onboarding_sessions", "players",
        ["verified_player_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 12: Update audit_issues references (discord_member_id FK target renamed)
    # -------------------------------------------------------------------------

    op.drop_constraint(
        "audit_issues_discord_member_id_fkey",
        "audit_issues",
        schema="guild_identity",
        type_="foreignkey",
    )
    op.drop_column("audit_issues", "person_id", schema="guild_identity")
    # Also clean up stale columns from old schema
    conn.execute(sa.text("""
        ALTER TABLE guild_identity.audit_issues
        DROP COLUMN IF EXISTS first_detected,
        DROP COLUMN IF EXISTS last_detected,
        DROP COLUMN IF EXISTS resolution_note
    """))
    op.create_foreign_key(
        "fk_audit_issues_discord_user",
        "audit_issues", "discord_users",
        ["discord_member_id"], ["id"],
        source_schema="guild_identity", referent_schema="guild_identity",
        ondelete="CASCADE",
    )

    # -------------------------------------------------------------------------
    # Step 13: Data migration — guild_members → players
    # -------------------------------------------------------------------------
    # Must happen BEFORE dropping guild_members.
    # Creates player rows for all existing guild_members, linking discord_users
    # and website users. Also creates player_characters from common.characters.

    conn.execute(sa.text("""
        -- Create a temp mapping table so we can use guild_member.id → player.id
        CREATE TEMP TABLE _member_player_map (
            member_id INTEGER PRIMARY KEY,
            player_id INTEGER
        ) ON COMMIT DROP;

        INSERT INTO guild_identity.players
            (display_name, discord_user_id, website_user_id, guild_rank_id,
             guild_rank_source, is_active, created_at, updated_at)
        SELECT
            COALESCE(gm.display_name, gm.discord_username),
            du.id,
            gm.user_id,
            gm.rank_id,
            'wow_character',
            TRUE,
            NOW(),
            NOW()
        FROM common.guild_members gm
        LEFT JOIN guild_identity.discord_users du ON du.discord_id = gm.discord_id;

        -- Build the mapping (match by discord_user_id or website_user_id)
        INSERT INTO _member_player_map (member_id, player_id)
        SELECT
            gm.id,
            p.id
        FROM common.guild_members gm
        JOIN guild_identity.players p ON (
            (gm.user_id IS NOT NULL AND p.website_user_id = gm.user_id)
            OR
            (gm.discord_id IS NOT NULL AND p.discord_user_id = (
                SELECT du.id FROM guild_identity.discord_users du
                WHERE du.discord_id = gm.discord_id
            ))
        )
        ON CONFLICT DO NOTHING;

        -- For members without discord or user link, match by display_name
        INSERT INTO _member_player_map (member_id, player_id)
        SELECT DISTINCT ON (gm.id)
            gm.id,
            p.id
        FROM common.guild_members gm
        JOIN guild_identity.players p
            ON p.display_name = COALESCE(gm.display_name, gm.discord_username)
        WHERE gm.id NOT IN (SELECT member_id FROM _member_player_map)
        ON CONFLICT DO NOTHING;
    """))

    # Create player_characters from common.characters + wow_characters
    conn.execute(sa.text("""
        INSERT INTO guild_identity.player_characters (player_id, character_id, created_at)
        SELECT DISTINCT
            m.player_id,
            wc.id,
            NOW()
        FROM common.characters c
        JOIN _member_player_map m ON m.member_id = c.member_id
        JOIN guild_identity.wow_characters wc
            ON LOWER(wc.character_name) = LOWER(c.name)
            AND wc.removed_at IS NULL
        WHERE m.player_id IS NOT NULL
        ON CONFLICT DO NOTHING;
    """))

    # Set main_character_id on players from common.characters main rows
    conn.execute(sa.text("""
        UPDATE guild_identity.players p
        SET main_character_id = wc.id
        FROM common.characters c
        JOIN _member_player_map m ON m.member_id = c.member_id
        JOIN guild_identity.wow_characters wc
            ON LOWER(wc.character_name) = LOWER(c.name)
            AND wc.removed_at IS NULL
        WHERE p.id = m.player_id
          AND c.main_alt = 'main'
          AND p.main_character_id IS NULL;
    """))

    # -------------------------------------------------------------------------
    # Step 14: Drop dead tables
    # -------------------------------------------------------------------------

    # Drop identity_links first (depends on wow_characters, discord_members)
    op.drop_table("identity_links", schema="guild_identity")

    # Drop common.characters (depends on common.guild_members)
    op.drop_table("characters", schema="common")

    # Drop common.guild_members last
    op.drop_table("guild_members", schema="common")


def downgrade() -> None:
    raise NotImplementedError(
        "Phase 2.7 migration is not safely reversible. "
        "Restore from database backup taken before running this migration."
    )
