-- =============================================================================
-- member_availability re-linking script
-- =============================================================================
-- Run this on prod to re-link the 133 orphaned member_availability rows to
-- their correct player IDs after the Phase 2.7 migration.
--
-- The migration nulled out player_id to clear stale guild_member IDs.
-- Use the notes and availability patterns below to identify each member,
-- then fill in the correct player_id from the players list at the bottom.
--
-- Usage:
--   sudo -u postgres psql patt_db -f scripts/relink_member_availability.sql
-- =============================================================================

-- Players reference (as of 2026-02-23):
--  1=Ron,          2=Tazz,      3=Mito,    4=Aethalin,  5=Clap on
--  6=greenappleblues, 7=DarkHallow, 8=Meg, 9=Miltonroe, 10=Shodoom
-- 11=Dragrik,     12=Nably,    13=Redvoker, 14=Porax,   15=Fort
-- 16=Delta,       17=Kronas,   18=Zeeffah, 19=Drizi,   20=Bamboly
-- 21=Worldofwyland, 22=Hit,    23=Basix,   24=Brox,    25=Samah
-- 26=MeowStorm,   27=Sirlos,   28=Trog,    29=BearWithMe, 30=robbiemac
-- 31=Skate,       32=Dart,     33=Revenge, 34=Corthan, 35=Rocket
-- 36=peonist,     37=Elrek,    38=Revenge96, 39=Widow, 40=Flintstoned
-- 41=Azz,         42=Shamlee,  43=Bam

BEGIN;

-- -----------------------------------------------------------------------
-- NOTED GROUPS (11 members with distinctive notes)
-- -----------------------------------------------------------------------

-- Group A (ids 106-112): "Sundays are my family night"
-- Mon=t Tue=t Wed=t Thu=t Fri=t Sat=t Sun=f (available most days, not Sunday)
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 106 AND 112;

-- Group B (ids 127-133): "No Tuesdays or Saturdays"
-- Mon=? Tue=f Wed=? Thu=? Fri=? Sat=f Sun=?
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 127 AND 133;

-- Group C (ids 141-147): Note is "2026-06-11T07:00:00.000Z" (date string, probably a form bug)
-- All available=t
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 141 AND 147;

-- Group D (ids 148-154): "Yet to be determined after baby is here"
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 148 AND 154;

-- Group E (ids 162-168): "Unsure with swapping work/family schedules"
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 162 AND 168;

-- Group F (ids 169-175): "I'm not desperate to raid if there are other tanks that want my spot..."
-- Mentions tanks — likely a tank player
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 169 AND 175;

-- Group G (ids 183-189): "Pretty willing to learn/play anything! Primary alts...Unholy/Blood DK, Monk...Affliction Lock"
-- Mentions specific alts — check against character roster
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 183 AND 189;

-- Group H (ids 190-196): "Flexible"
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 190 AND 196;

-- Group I (ids 218-224): "I'm not the best and still kinda new. My gear lvl is 122..."
-- Newer/lower gear player
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 218 AND 224;

-- Group J (ids 232-238): "Wednesday is a go like 65%"
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 232 AND 238;

-- Group K (ids 239-245): "Hi! I cannot completely confirm until actual raid dates are chosen. I plan to raid with 2 guilds."
-- Raids with 2 guilds — distinctive
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 239 AND 245;

-- -----------------------------------------------------------------------
-- NO-NOTE GROUPS (8 members, identified by availability pattern)
-- -----------------------------------------------------------------------

-- Group L (ids 113-119): Mon=t Tue=f Wed=t Thu=t Fri=t Sat=t Sun=f
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 113 AND 119;

-- Group M (ids 120-126): Mon=f Tue=f Wed=f Thu=t Fri=t Sat=t Sun=t
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 120 AND 126;

-- Group N (ids 134-140): Mon=f Tue=f Wed=f Thu=t Fri=t Sat=t Sun=f
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 134 AND 140;

-- Group O (ids 155-161): Mon=f Tue=f Wed=f Thu=f Fri=f Sat=t Sun=t  (weekend only)
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 155 AND 161;

-- Group P (ids 176-182): Mon=f Tue=f Wed=f Thu=f Fri=t Sat=t Sun=f  auto_signup=TRUE
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 176 AND 182;

-- Group Q (ids 197-203): Mon=f Tue=f Wed=f Thu=f Fri=t Sat=t Sun=f  auto_signup=FALSE
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 197 AND 203;

-- Group R (ids 204-210): All days available (Mon-Sun all=t)
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 204 AND 210;

-- Group S (ids 246-252): Mon=f Tue=f Wed=f Thu=f Fri=t Sat=t Sun=t  auto_signup=TRUE (added Feb 23)
UPDATE common.member_availability SET player_id = NULL  -- REPLACE NULL with correct player id
WHERE id BETWEEN 246 AND 252;

-- -----------------------------------------------------------------------
-- VERIFY (run this before COMMIT to check your work)
-- -----------------------------------------------------------------------
SELECT player_id, COUNT(*) AS days,
       LEFT(notes, 50) AS note_preview
FROM common.member_availability
GROUP BY player_id, notes
ORDER BY player_id NULLS LAST;

-- If everything looks correct, COMMIT. Otherwise ROLLBACK.
-- COMMIT;
-- ROLLBACK;
