# Phase 4.4.3 — Onboarding Activation & OAuth Integration

## Goal

Wire up the dormant onboarding system so that when a new member joins the Discord,
they receive a DM that walks them through registration and Battle.net linking.
The onboarding flow ends when OAuth completes — at that point, the member has a site
account, a Discord link, and a fully verified character roster, all without officer
intervention.

---

## Prerequisites

- Phase 4.4.1 complete (Battle.net OAuth flow and settings page)
- Phase 4.4.2 complete (character auto-claim on OAuth)
- Discord bot running and connected to the guild
- Existing onboarding code in `src/sv_common/guild_sync/onboarding/`:
  - `conversation.py` — Discord DM conversation flow
  - `provisioner.py` — Creates user account + invite code
  - `deadline_checker.py` — Detects stale onboarding sessions
  - `commands.py` — Admin slash commands (`/onboarding`, `/resend-invite`)

---

## No New Migration

The `guild_identity.onboarding_sessions` table already exists and is ready.
This phase is entirely about activating dormant code and updating the conversation flow.

---

## Current State of the Onboarding System

The system was built in Phase 2.6 but never activated. What exists:

- `conversation.py` — A multi-step Discord DM bot conversation that:
  1. Greets the new member
  2. Asks them to confirm their main character name
  3. Generates and DMs an invite code
  4. Prompts them to register at the site URL
  5. Marks onboarding complete when the user registers

- `provisioner.py` — Creates the site user account and invite code when the
  conversation reaches the registration step

- `deadline_checker.py` — Runs on a schedule, DMs a reminder if the member hasn't
  completed registration within N hours

- `commands.py` — Admin commands for managing onboarding manually

**What is missing (not wired):**
- `on_member_join` event in the Discord bot is not connected to the onboarding flow

---

## Task 1: Wire `on_member_join`

### File: `src/sv_common/discord/bot.py` (or equivalent bot entry point)

Add the `on_member_join` event listener:

```python
@bot.event
async def on_member_join(member: discord.Member):
    """
    Triggered when a new member joins the Discord.
    Starts the onboarding conversation for the new member.
    """
    if member.guild.id != int(config.discord_guild_id):
        return  # Ignore events from other guilds (shouldn't happen, but guard anyway)

    await start_onboarding_conversation(member)
```

Connect `start_onboarding_conversation()` from `conversation.py`.

---

## Task 2: Update Conversation Flow — Add OAuth Step

### File: `src/sv_common/guild_sync/onboarding/conversation.py`

The existing flow ends at "register on the site." Add a new final step: Battle.net linking.

**Updated flow:**

```
Step 1: Greeting
────────────────
"Welcome to Pull All The Things, {display_name}! 👋
 I'm the guild bot. I'll get you set up in just a few steps."

Step 2: Invite Code Delivery
─────────────────────────────
"Here's your invite code: {code}
 Head to {site_url}/register and use this code to create your account."

Step 3: Registration Confirmation (wait for webhook signal)
────────────────────────────────────────────────────────────
[Bot polls or receives a callback when registration completes]
"Great, you're registered! One last step..."

Step 4: Battle.net Link Prompt
────────────────────────────────
"Connect your Battle.net account so we can automatically
 find your characters:

 👉 {site_url}/auth/battlenet

 This takes about 10 seconds — just click 'Approve' on
 Blizzard's page and you're done."

Step 5: Completion (wait for OAuth callback signal)
────────────────────────────────────────────────────
[Bot receives signal when sync_bnet_characters() completes]
"You're all set! ✅
 Found {n} characters on Sen'jin linked to your profile.
 Check your character roster at {site_url}/settings/characters"
```

**Skipping OAuth is allowed.** If the member doesn't complete OAuth within 24 hours of
registration, the `deadline_checker` sends one reminder. After 48 hours, the session is
marked `abandoned_oauth` (a new status value) and the member continues with no characters
linked. They can always link later from Settings.

---

## Task 3: Signal Between Site and Bot

When a member completes OAuth in the browser, the bot needs to know so it can send
the completion DM. Two options:

**Option A (Recommended): Bot polls `onboarding_sessions`**
After registration, the bot sets a timer to check the `onboarding_sessions.status` every
60 seconds. When it sees `status = 'oauth_complete'`, it sends the completion DM and
stops polling. Timeout: 10 minutes of polling (the bot gives up and moves on; the member
already has a working account).

**Option B: In-process event**
Since the bot runs in the same process as the FastAPI app, `sync_bnet_characters()` can
directly call a bot method. This is tighter coupling but simpler code.

**Recommended: Option A.** Polling is more resilient and keeps the bot/app layers decoupled.
The 60-second polling delay is acceptable — this is a one-time flow, not realtime.

### Update `onboarding_sessions` Status Values

Existing statuses: `pending`, `registered`, `complete`, `expired`

Add new statuses:
- `oauth_pending` — registered, waiting for Battle.net link
- `oauth_complete` — Battle.net linked, all done
- `abandoned_oauth` — registered but never linked OAuth (member chose not to)

### Signal from OAuth Callback

In `bnet_auth_routes.py`, after `sync_bnet_characters()` completes, update the
onboarding session if one exists for this player:

```python
# After sync_bnet_characters():
await update_onboarding_status(pool, player_id, "oauth_complete",
                                character_count=sync_stats["linked"])
```

---

## Task 4: Update `deadline_checker.py`

### File: `src/sv_common/guild_sync/onboarding/deadline_checker.py`

Add handling for the new `oauth_pending` status:

```python
# Existing: remind members who haven't registered
if status == 'pending' and hours_since_created > 24:
    send_registration_reminder(member)

# New: remind members who registered but haven't done OAuth
if status == 'oauth_pending' and hours_since_registered > 24:
    send_oauth_reminder(member, site_url)

# New: abandon OAuth after 48 hours — they chose not to, that's fine
if status == 'oauth_pending' and hours_since_registered > 48:
    set_status(session, 'abandoned_oauth')
    send_no_oauth_completion(member)  # "You're all set without Battle.net — add chars manually if you like"
```

The `abandoned_oauth` completion message is friendly, not a failure:
"You're registered and ready to go! If you ever want to connect Battle.net to
automatically link your characters, you can do it anytime from Settings."

---

## Task 5: Admin Commands

### File: `src/sv_common/guild_sync/onboarding/commands.py`

These commands already exist. Verify they still work after the flow update and add:

**`/resend-oauth {member}`**
Re-sends the Battle.net link DM to a member stuck at `oauth_pending`. Useful if they
lost the original DM.

---

## Task 6: Feature Flag

The onboarding system should remain gateable so future Guild Portal deployments can
enable/disable it via site_config.

### File: `src/sv_common/db/models.py` (SiteConfig)

Add to site_config (migration is a future concern — use an existing flag or add as part
of a combined migration if needed):

```python
enable_onboarding: bool = True  # Whether on_member_join triggers the flow
```

Check this flag in `on_member_join` before starting the flow. Officers can disable
onboarding during a migration or testing period without redeploying.

---

## Testing Considerations

The onboarding system sends Discord DMs, which requires a live bot. Tests for the
conversation flow should use a mock `discord.Member` and mock DM channel. The existing
skip-gating pattern (same as bot DM gate tests) applies here.

- Unit test `on_member_join` calls `start_onboarding_conversation` (mock bot)
- Unit test conversation flow steps with mock DM channel
- Unit test `update_onboarding_status()` sets correct status in DB
- Unit test `deadline_checker` handles `oauth_pending` → reminder at 24h → abandon at 48h
- Unit test OAuth callback sets `oauth_complete` in `onboarding_sessions` if session exists
- Unit test OAuth callback with no existing onboarding session (member registered manually) — no error
- All existing tests pass

---

## Deliverables Checklist

- [ ] `on_member_join` wired in Discord bot → calls `start_onboarding_conversation()`
- [ ] Conversation flow updated with OAuth step (Step 4) and completion DM (Step 5)
- [ ] New onboarding status values: `oauth_pending`, `oauth_complete`, `abandoned_oauth`
- [ ] OAuth callback updates `onboarding_sessions` status when session exists
- [ ] Bot polling loop: checks for `oauth_complete` every 60s, sends completion DM
- [ ] `deadline_checker.py` handles `oauth_pending` → reminder at 24h → abandon at 48h
- [ ] Friendly "abandoned_oauth" completion message (not a failure)
- [ ] `/resend-oauth {member}` admin command
- [ ] `enable_onboarding` feature flag checked in `on_member_join`
- [ ] Tests
