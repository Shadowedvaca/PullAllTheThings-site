"""Demon Hunter: add Devourer spec + update hero talent names

Revision ID: 0073
Revises: 0072
Create Date: 2026-04-05

New expansion added Devourer as a third Demon Hunter spec and reshaped the
hero talent trees:
  Havoc:     Aldrachi Reaver, Scarred      (Felscarred renamed to Scarred)
  Vengeance: Aldrachi Reaver, Annihilator  (Felscarred replaced by Annihilator)
  Devourer:  Annihilator, Scarred          (new spec)

Also clears stale Demon Hunter BIS scrape targets so discover_targets + sync
rebuild them with correct slugs.
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Rename Felscarred → Scarred for Havoc ---
    op.execute("""
        UPDATE guild_identity.hero_talents ht
           SET name = 'Scarred', slug = 'scarred'
          FROM guild_identity.specializations sp
          JOIN guild_identity.classes c ON c.id = sp.class_id
         WHERE ht.spec_id = sp.id
           AND sp.name = 'Havoc'
           AND c.name = 'Demon Hunter'
           AND ht.name = 'Felscarred'
    """)

    # --- Replace Vengeance Felscarred with Annihilator ---
    op.execute("""
        UPDATE guild_identity.hero_talents ht
           SET name = 'Annihilator', slug = 'annihilator'
          FROM guild_identity.specializations sp
          JOIN guild_identity.classes c ON c.id = sp.class_id
         WHERE ht.spec_id = sp.id
           AND sp.name = 'Vengeance'
           AND c.name = 'Demon Hunter'
           AND ht.name = 'Felscarred'
    """)

    # --- Add Devourer spec (Melee DPS role, same as Havoc) ---
    op.execute("""
        INSERT INTO guild_identity.specializations (class_id, name, default_role_id)
        SELECT c.id, 'Devourer', r.id
          FROM guild_identity.classes c
          JOIN guild_identity.roles r ON r.name = 'Melee DPS'
         WHERE c.name = 'Demon Hunter'
        ON CONFLICT (class_id, name) DO NOTHING
    """)

    # --- Add Devourer hero talents: Annihilator + Scarred ---
    op.execute("""
        INSERT INTO guild_identity.hero_talents (spec_id, name, slug)
        SELECT sp.id, ht.name, ht.slug
          FROM (VALUES
              ('Annihilator', 'annihilator'),
              ('Scarred',     'scarred')
          ) AS ht(name, slug)
          JOIN guild_identity.specializations sp ON sp.name = 'Devourer'
          JOIN guild_identity.classes c ON c.id = sp.class_id AND c.name = 'Demon Hunter'
        ON CONFLICT (spec_id, name) DO NOTHING
    """)

    # --- Clear stale DH targets so they rebuild cleanly ---
    op.execute("""
        DELETE FROM guild_identity.bis_scrape_log
         WHERE target_id IN (
             SELECT t.id FROM guild_identity.bis_scrape_targets t
              JOIN guild_identity.specializations sp ON sp.id = t.spec_id
              JOIN guild_identity.classes c ON c.id = sp.class_id
             WHERE c.name = 'Demon Hunter'
         )
    """)
    op.execute("""
        DELETE FROM guild_identity.bis_scrape_targets t
         USING guild_identity.specializations sp
              ,guild_identity.classes c
         WHERE t.spec_id = sp.id
           AND sp.class_id = c.id
           AND c.name = 'Demon Hunter'
    """)


def downgrade() -> None:
    # Remove Devourer hero talents and spec
    op.execute("""
        DELETE FROM guild_identity.hero_talents ht
         USING guild_identity.specializations sp
              ,guild_identity.classes c
         WHERE ht.spec_id = sp.id
           AND sp.class_id = c.id
           AND sp.name = 'Devourer'
           AND c.name = 'Demon Hunter'
    """)
    op.execute("""
        DELETE FROM guild_identity.specializations sp
         USING guild_identity.classes c
         WHERE sp.class_id = c.id
           AND sp.name = 'Devourer'
           AND c.name = 'Demon Hunter'
    """)

    # Restore Vengeance Felscarred
    op.execute("""
        UPDATE guild_identity.hero_talents ht
           SET name = 'Felscarred', slug = 'felscarred'
          FROM guild_identity.specializations sp
          JOIN guild_identity.classes c ON c.id = sp.class_id
         WHERE ht.spec_id = sp.id
           AND sp.name = 'Vengeance'
           AND c.name = 'Demon Hunter'
           AND ht.name = 'Annihilator'
    """)

    # Restore Havoc Felscarred
    op.execute("""
        UPDATE guild_identity.hero_talents ht
           SET name = 'Felscarred', slug = 'felscarred'
          FROM guild_identity.specializations sp
          JOIN guild_identity.classes c ON c.id = sp.class_id
         WHERE ht.spec_id = sp.id
           AND sp.name = 'Havoc'
           AND c.name = 'Demon Hunter'
           AND ht.name = 'Scarred'
    """)
