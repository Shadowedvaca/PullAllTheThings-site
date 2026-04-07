"""Gear Plan Phase 1B — seed hero_talents + gear_plan nav entry

Revision ID: 0067
Revises: 0066
Create Date: 2026-04-04

Seeds guild_identity.hero_talents with all TWW hero talent trees
(36 specs × 2 hero talents each = 72 rows).

Also inserts the gear_plan screen_permission row so the admin
sidebar shows the BIS Sync link.
"""

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Seed hero_talents
    #    Join to specializations + classes by name so we don't need to
    #    hard-code IDs (which differ between environments).
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO guild_identity.hero_talents (spec_id, name, slug)
        SELECT s.id, vals.ht_name, vals.ht_slug
        FROM (VALUES
            -- Death Knight
            ('Death Knight', 'Blood',        'San''layn',                 'san_layn'),
            ('Death Knight', 'Blood',        'Deathbringer',              'deathbringer'),
            ('Death Knight', 'Frost',        'Rider of the Apocalypse',   'rider_of_the_apocalypse'),
            ('Death Knight', 'Frost',        'Deathbringer',              'deathbringer'),
            ('Death Knight', 'Unholy',       'San''layn',                 'san_layn'),
            ('Death Knight', 'Unholy',       'Rider of the Apocalypse',   'rider_of_the_apocalypse'),
            -- Demon Hunter
            ('Demon Hunter', 'Havoc',        'Aldrachi Reaver',           'aldrachi_reaver'),
            ('Demon Hunter', 'Havoc',        'Felscarred',                'felscarred'),
            ('Demon Hunter', 'Vengeance',    'Aldrachi Reaver',           'aldrachi_reaver'),
            ('Demon Hunter', 'Vengeance',    'Felscarred',                'felscarred'),
            -- Druid
            ('Druid', 'Balance',             'Elune''s Chosen',           'elunes_chosen'),
            ('Druid', 'Balance',             'Keeper of the Grove',       'keeper_of_the_grove'),
            ('Druid', 'Feral',               'Druid of the Claw',         'druid_of_the_claw'),
            ('Druid', 'Feral',               'Wildstalker',               'wildstalker'),
            ('Druid', 'Guardian',            'Druid of the Claw',         'druid_of_the_claw'),
            ('Druid', 'Guardian',            'Elune''s Chosen',           'elunes_chosen'),
            ('Druid', 'Restoration',         'Keeper of the Grove',       'keeper_of_the_grove'),
            ('Druid', 'Restoration',         'Wildstalker',               'wildstalker'),
            -- Evoker
            ('Evoker', 'Devastation',        'Scalecommander',            'scalecommander'),
            ('Evoker', 'Devastation',        'Flameshaper',               'flameshaper'),
            ('Evoker', 'Preservation',       'Chronowarden',              'chronowarden'),
            ('Evoker', 'Preservation',       'Flameshaper',               'flameshaper'),
            ('Evoker', 'Augmentation',       'Scalecommander',            'scalecommander'),
            ('Evoker', 'Augmentation',       'Chronowarden',              'chronowarden'),
            -- Hunter
            ('Hunter', 'Beast Mastery',      'Pack Leader',               'pack_leader'),
            ('Hunter', 'Beast Mastery',      'Dark Ranger',               'dark_ranger'),
            ('Hunter', 'Marksmanship',       'Sentinel',                  'sentinel'),
            ('Hunter', 'Marksmanship',       'Dark Ranger',               'dark_ranger'),
            ('Hunter', 'Survival',           'Pack Leader',               'pack_leader'),
            ('Hunter', 'Survival',           'Sentinel',                  'sentinel'),
            -- Mage
            ('Mage', 'Arcane',               'Spellslinger',              'spellslinger'),
            ('Mage', 'Arcane',               'Sunfury',                   'sunfury'),
            ('Mage', 'Fire',                 'Frostfire',                 'frostfire'),
            ('Mage', 'Fire',                 'Sunfury',                   'sunfury'),
            ('Mage', 'Frost',                'Spellslinger',              'spellslinger'),
            ('Mage', 'Frost',                'Frostfire',                 'frostfire'),
            -- Monk
            ('Monk', 'Brewmaster',           'Master of Harmony',         'master_of_harmony'),
            ('Monk', 'Brewmaster',           'Conduit of the Celestials', 'conduit_of_the_celestials'),
            ('Monk', 'Mistweaver',           'Master of Harmony',         'master_of_harmony'),
            ('Monk', 'Mistweaver',           'Shado-pan',                 'shado_pan'),
            ('Monk', 'Windwalker',           'Conduit of the Celestials', 'conduit_of_the_celestials'),
            ('Monk', 'Windwalker',           'Shado-pan',                 'shado_pan'),
            -- Paladin
            ('Paladin', 'Holy',              'Herald of the Sun',         'herald_of_the_sun'),
            ('Paladin', 'Holy',              'Lightsmith',                'lightsmith'),
            ('Paladin', 'Protection',        'Lightsmith',                'lightsmith'),
            ('Paladin', 'Protection',        'Templar',                   'templar'),
            ('Paladin', 'Retribution',       'Templar',                   'templar'),
            ('Paladin', 'Retribution',       'Herald of the Sun',         'herald_of_the_sun'),
            -- Priest
            ('Priest', 'Discipline',         'Archon',                    'archon'),
            ('Priest', 'Discipline',         'Oracle',                    'oracle'),
            ('Priest', 'Holy',               'Archon',                    'archon'),
            ('Priest', 'Holy',               'Oracle',                    'oracle'),
            ('Priest', 'Shadow',             'Archon',                    'archon'),
            ('Priest', 'Shadow',             'Voidweaver',                'voidweaver'),
            -- Rogue
            ('Rogue', 'Assassination',       'Fatebound',                 'fatebound'),
            ('Rogue', 'Assassination',       'Trickster',                 'trickster'),
            ('Rogue', 'Outlaw',              'Fatebound',                 'fatebound'),
            ('Rogue', 'Outlaw',              'Deathstalker',              'deathstalker'),
            ('Rogue', 'Subtlety',            'Trickster',                 'trickster'),
            ('Rogue', 'Subtlety',            'Deathstalker',              'deathstalker'),
            -- Shaman
            ('Shaman', 'Elemental',          'Farseer',                   'farseer'),
            ('Shaman', 'Elemental',          'Totemic',                   'totemic'),
            ('Shaman', 'Enhancement',        'Stormbringer',              'stormbringer'),
            ('Shaman', 'Enhancement',        'Totemic',                   'totemic'),
            ('Shaman', 'Restoration',        'Farseer',                   'farseer'),
            ('Shaman', 'Restoration',        'Totemic',                   'totemic'),
            -- Warlock
            ('Warlock', 'Affliction',        'Hellcaller',                'hellcaller'),
            ('Warlock', 'Affliction',        'Soul Harvester',            'soul_harvester'),
            ('Warlock', 'Demonology',        'Diabolist',                 'diabolist'),
            ('Warlock', 'Demonology',        'Soul Harvester',            'soul_harvester'),
            ('Warlock', 'Destruction',       'Hellcaller',                'hellcaller'),
            ('Warlock', 'Destruction',       'Diabolist',                 'diabolist'),
            -- Warrior
            ('Warrior', 'Arms',              'Slayer',                    'slayer'),
            ('Warrior', 'Arms',              'Mountain Thane',            'mountain_thane'),
            ('Warrior', 'Fury',              'Slayer',                    'slayer'),
            ('Warrior', 'Fury',              'Mountain Thane',            'mountain_thane'),
            ('Warrior', 'Protection',        'Colossus',                  'colossus'),
            ('Warrior', 'Protection',        'Mountain Thane',            'mountain_thane')
        ) AS vals(class_name, spec_name, ht_name, ht_slug)
        JOIN guild_identity.specializations s ON s.name = vals.spec_name
        JOIN guild_identity.classes c         ON c.id = s.class_id AND c.name = vals.class_name
        ON CONFLICT (spec_id, name) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # 2. Screen permission — gear_plan (GL-only, level 5)
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('gear_plan', 'Gear Plan / BIS', '/admin/gear-plan',
             'guild_tools', 'Guild Tools', 2, 10, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'gear_plan'")
    op.execute("DELETE FROM guild_identity.hero_talents")
