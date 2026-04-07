"""Gear Plan — Phase 1A foundation

Revision ID: 0066
Revises: 0065
Create Date: 2026-04-03

Adds 10 tables for the gear plan feature plus last_equipment_sync column
on wow_characters.  Tables:
  guild_identity.wow_items             — cached item metadata (Wowhead/Blizzard)
  guild_identity.item_sources          — boss / dungeon drop sources
  guild_identity.hero_talents          — hero-talent reference (spec → slug)
  guild_identity.bis_list_sources      — named BIS list providers
  guild_identity.bis_list_entries      — BIS items per spec × hero × slot
  guild_identity.character_equipment   — current equipped gear per slot
  guild_identity.gear_plans            — player gear plan per character
  guild_identity.gear_plan_slots       — desired item per slot
  guild_identity.bis_scrape_targets    — URL map for automated BIS extraction
  guild_identity.bis_scrape_log        — extraction attempt history
"""
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. last_equipment_sync on wow_characters
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE guild_identity.wow_characters
            ADD COLUMN IF NOT EXISTS last_equipment_sync
                TIMESTAMP WITH TIME ZONE DEFAULT NULL
        """
    )

    # ------------------------------------------------------------------
    # 2. wow_items — cached item metadata
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.wow_items (
            id                    SERIAL PRIMARY KEY,
            blizzard_item_id      INTEGER NOT NULL UNIQUE,
            name                  VARCHAR(200) NOT NULL,
            icon_url              VARCHAR(500),
            slot_type             VARCHAR(20) NOT NULL,
            armor_type            VARCHAR(20),
            weapon_type           VARCHAR(30),
            wowhead_tooltip_html  TEXT,
            fetched_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )

    # ------------------------------------------------------------------
    # 3. item_sources — where items drop
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.item_sources (
            id                    SERIAL PRIMARY KEY,
            item_id               INTEGER NOT NULL
                                      REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
            source_type           VARCHAR(20) NOT NULL
                                      CHECK (source_type IN (
                                          'raid_boss','dungeon','profession',
                                          'world','pvp','other')),
            source_name           VARCHAR(100) NOT NULL,
            source_instance       VARCHAR(100),
            blizzard_encounter_id INTEGER,
            blizzard_instance_id  INTEGER,
            quality_tracks        TEXT[]  NOT NULL DEFAULT '{}',
            UNIQUE (item_id, source_type, source_name)
        )
        """
    )

    # ------------------------------------------------------------------
    # 4. hero_talents — reference table for hero-talent trees
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.hero_talents (
            id       SERIAL PRIMARY KEY,
            spec_id  INTEGER NOT NULL
                         REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
            name     VARCHAR(100) NOT NULL,
            slug     VARCHAR(50)  NOT NULL,
            UNIQUE (spec_id, name)
        )
        """
    )

    # ------------------------------------------------------------------
    # 5. bis_list_sources — named BIS list providers
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.bis_list_sources (
            id           SERIAL PRIMARY KEY,
            name         VARCHAR(100) UNIQUE NOT NULL,
            short_label  VARCHAR(30),
            origin       VARCHAR(50),
            content_type VARCHAR(20),
            is_default   BOOLEAN NOT NULL DEFAULT FALSE,
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            last_synced  TIMESTAMP WITH TIME ZONE
        )
        """
    )

    # Seed the three primary BIS sources
    op.execute(
        """
        INSERT INTO guild_identity.bis_list_sources
            (name, short_label, origin, content_type, is_default, is_active, sort_order)
        VALUES
            ('Archon Raid',       'Archon R',  'archon',    'raid',        TRUE,  TRUE,  10),
            ('Archon M+',         'Archon M+', 'archon',    'mythic_plus', FALSE, TRUE,  20),
            ('Wowhead Overall',   'Wowhead',   'wowhead',   'overall',     FALSE, TRUE,  30),
            ('Icy Veins Raid',    'IV Raid',   'icy_veins', 'raid',        FALSE, TRUE,  40),
            ('Icy Veins M+',      'IV M+',     'icy_veins', 'mythic_plus', FALSE, TRUE,  50)
        """
    )

    # ------------------------------------------------------------------
    # 6. bis_list_entries — BIS items per spec × hero talent × slot
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.bis_list_entries (
            id              SERIAL PRIMARY KEY,
            source_id       INTEGER NOT NULL
                                REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
            spec_id         INTEGER NOT NULL
                                REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
            hero_talent_id  INTEGER
                                REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
            slot            VARCHAR(20) NOT NULL,
            item_id         INTEGER NOT NULL
                                REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
            priority        INTEGER NOT NULL DEFAULT 1,
            notes           TEXT,
            UNIQUE (source_id, spec_id, hero_talent_id, slot, item_id)
        )
        """
    )

    # ------------------------------------------------------------------
    # 7. character_equipment — current equipped gear per slot
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.character_equipment (
            id               SERIAL PRIMARY KEY,
            character_id     INTEGER NOT NULL
                                 REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
            slot             VARCHAR(20) NOT NULL,
            blizzard_item_id INTEGER NOT NULL,
            item_id          INTEGER
                                 REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL,
            item_name        VARCHAR(200),
            item_level       INTEGER NOT NULL,
            quality_track    VARCHAR(1),
            bonus_ids        INTEGER[]  NOT NULL DEFAULT '{}',
            enchant_id       INTEGER,
            gem_ids          INTEGER[]  NOT NULL DEFAULT '{}',
            synced_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (character_id, slot)
        )
        """
    )

    # ------------------------------------------------------------------
    # 8. gear_plans — player's gear plan per character
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.gear_plans (
            id              SERIAL PRIMARY KEY,
            player_id       INTEGER NOT NULL
                                REFERENCES guild_identity.players(id) ON DELETE CASCADE,
            character_id    INTEGER NOT NULL
                                REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
            spec_id         INTEGER
                                REFERENCES guild_identity.specializations(id) ON DELETE SET NULL,
            hero_talent_id  INTEGER
                                REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
            bis_source_id   INTEGER
                                REFERENCES guild_identity.bis_list_sources(id) ON DELETE SET NULL,
            simc_profile    TEXT,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (player_id, character_id)
        )
        """
    )

    # ------------------------------------------------------------------
    # 9. gear_plan_slots — per-slot item selections
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.gear_plan_slots (
            id               SERIAL PRIMARY KEY,
            plan_id          INTEGER NOT NULL
                                 REFERENCES guild_identity.gear_plans(id) ON DELETE CASCADE,
            slot             VARCHAR(20) NOT NULL,
            desired_item_id  INTEGER
                                 REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL,
            blizzard_item_id INTEGER,
            item_name        VARCHAR(200),
            is_locked        BOOLEAN NOT NULL DEFAULT FALSE,
            notes            TEXT,
            UNIQUE (plan_id, slot)
        )
        """
    )

    # ------------------------------------------------------------------
    # 10. bis_scrape_targets — URL map for automated BIS extraction
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.bis_scrape_targets (
            id                  SERIAL PRIMARY KEY,
            source_id           INTEGER NOT NULL
                                    REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
            spec_id             INTEGER NOT NULL
                                    REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
            hero_talent_id      INTEGER
                                    REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
            content_type        VARCHAR(20),
            url                 TEXT,
            preferred_technique VARCHAR(20)
                                    CHECK (preferred_technique IN (
                                        'json_embed','wh_gatherer','html_parse','simc','manual')),
            status              VARCHAR(20) NOT NULL DEFAULT 'pending',
            items_found         INTEGER NOT NULL DEFAULT 0,
            last_fetched        TIMESTAMP WITH TIME ZONE,
            UNIQUE (source_id, spec_id, hero_talent_id, content_type)
        )
        """
    )

    # ------------------------------------------------------------------
    # 11. bis_scrape_log — extraction attempt history
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE guild_identity.bis_scrape_log (
            id           SERIAL PRIMARY KEY,
            target_id    INTEGER NOT NULL
                             REFERENCES guild_identity.bis_scrape_targets(id) ON DELETE CASCADE,
            technique    VARCHAR(20) NOT NULL,
            status       VARCHAR(20) NOT NULL
                             CHECK (status IN ('success','partial','failed')),
            items_found  INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guild_identity.bis_scrape_log")
    op.execute("DROP TABLE IF EXISTS guild_identity.bis_scrape_targets")
    op.execute("DROP TABLE IF EXISTS guild_identity.gear_plan_slots")
    op.execute("DROP TABLE IF EXISTS guild_identity.gear_plans")
    op.execute("DROP TABLE IF EXISTS guild_identity.character_equipment")
    op.execute("DROP TABLE IF EXISTS guild_identity.bis_list_entries")
    op.execute("DROP TABLE IF EXISTS guild_identity.bis_list_sources")
    op.execute("DROP TABLE IF EXISTS guild_identity.hero_talents")
    op.execute("DROP TABLE IF EXISTS guild_identity.item_sources")
    op.execute("DROP TABLE IF EXISTS guild_identity.wow_items")
    op.execute(
        """
        ALTER TABLE guild_identity.wow_characters
            DROP COLUMN IF EXISTS last_equipment_sync
        """
    )
