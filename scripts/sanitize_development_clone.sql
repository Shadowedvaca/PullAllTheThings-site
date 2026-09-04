-- Sanitize a production-shaped clone before Development application access.
-- Preserve content/item relationships and stable internal IDs; remove credentials,
-- direct identifiers, free-form member text, auth sessions, and social submissions.
BEGIN;

UPDATE common.users u
   SET email = 'cloned-user-' || u.id || '@example.invalid',
       phone = NULL,
       password_hash = '!development-clone-disabled-' || u.id,
       is_active = FALSE,
       last_active_at = NULL,
       last_login_at = NULL,
       login_count = 0
 WHERE NOT EXISTS (
       SELECT 1 FROM patt_refresh_admin_credentials a WHERE a.email = u.email
   );

UPDATE common.users u
   SET password_hash = a.password_hash, phone = NULL, is_active = TRUE
  FROM patt_refresh_admin_credentials a
 WHERE a.email = u.email;

TRUNCATE TABLE common.user_activity, common.feedback_submissions,
               common.invite_codes, common.auth_sessions RESTART IDENTITY CASCADE;

UPDATE guild_identity.discord_users
   SET discord_id = 'clone-' || id,
       username = 'cloned-user-' || id,
       display_name = 'Cloned User ' || id,
       highest_guild_role = NULL,
       all_guild_roles = '{}',
       joined_server_at = NULL,
       removed_at = NULL,
       no_guild_role_since = NULL;

UPDATE guild_identity.players
   SET display_name = 'Cloned Player ' || id,
       notes = NULL,
       discord_user_id = NULL,
       website_user_id = CASE
           WHEN website_user_id IN (
               SELECT u.id FROM common.users u
               JOIN patt_refresh_admin_credentials a ON a.email = u.email
           ) THEN website_user_id ELSE NULL END;

UPDATE guild_identity.wow_characters
   SET character_name = 'Clonedchar' || id,
       guild_note = NULL,
       officer_note = NULL,
       last_login_timestamp = NULL;

UPDATE guild_identity.character_name_history
   SET character_name = 'Clonedchar' || wow_character_id || 'h' || id;

TRUNCATE TABLE guild_identity.battlenet_accounts,
               guild_identity.onboarding_sessions RESTART IDENTITY CASCADE;

UPDATE guild_identity.gear_plan_slots SET notes = NULL;
UPDATE guild_identity.player_action_log SET character_name = NULL, details = NULL;
UPDATE guild_identity.raid_reports
   SET report_code = 'clone-' || id,
       title = NULL,
       owner_name = NULL,
       attendees = NULL,
       report_url = NULL;
UPDATE guild_identity.raiderio_profiles SET profile_url = NULL;
UPDATE guild_identity.sync_log SET error_message = NULL;
UPDATE guild_identity.audit_issues SET details = NULL;

UPDATE patt.raid_attendance SET noted_absence = FALSE;
UPDATE patt.raid_events
   SET discord_channel_id = NULL,
       log_url = NULL,
       notes = NULL,
       raid_helper_payload = NULL;
TRUNCATE TABLE patt.voice_attendance_log, patt.guild_quotes,
               patt.quote_subjects,
               patt.campaign_entries, patt.campaigns,
               patt.contest_agent_log, patt.recruiting_submissions,
               patt.recruiting_contests RESTART IDENTITY CASCADE;

DROP TABLE patt_refresh_admin_credentials;

COMMIT;
