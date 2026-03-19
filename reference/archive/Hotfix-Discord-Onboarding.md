# Discord Onboarding — Reference Guide & Hotfix Notes

> Replaces `docs/DISCORD.md`. Covers the current onboarding flow, known edge cases,
> officer commands, and the updated guild-facing quick guide.
>
> Updated: 2026-03-17

---

## 1. How Onboarding Works (Current Flow)

When a member joins the Discord server, the bot fires `on_member_join` and starts the
`OnboardingConversation` flow automatically (requires `enable_onboarding = TRUE` in
Site Config).

### Happy Path

```
Member joins server
  → Bot creates onboarding_session (state: awaiting_dm)
  → Bot sends welcome DM: "Have you joined the guild in WoW?"
    → YES → "What's your main character name?" → "Any alts?"
             → Bot matches name in wow_characters roster
             → state: pending_verification
             → Auto-provisions: invite code sent via DM, Discord role set
             → state: oauth_pending
             → Bot DMs Battle.net OAuth link
             → Member clicks, connects BNet → characters auto-linked
             → state: oauth_complete
             → Bot DMs "You're all set!"
    → NO  → "Reply when you join, or use /pattsync in any channel"
             → state: pending_verification, deadline_at = +24h
             → Deadline checker follows up every 30 min
```

### What Triggers Auto-Provisioning

The provisioner runs when the session reaches `state = 'verified'`. It:
- Links the character to the player record
- Assigns the correct Discord role (based on WoW rank)
- Generates a website invite code and DMs it to the member

---

## 2. Known Issue: DMs Closed (the "huttboles" scenario)

**Root cause:** Discord allows users to block DMs from server members who aren't friends.
When this setting is on, `member.create_dm()` / `dm.send()` raises `discord.Forbidden`.

### What Happens Today

```
Member joins server (DMs closed)
  → Bot tries to send welcome DM
  → discord.Forbidden raised
  → WARNING logged: "Cannot DM {name} — DMs closed"
  → Session set to state: "declined"
  → No invite code sent
  → No notification to the member
```

The Discord sync runs independently and will still assign the correct Discord role based on WoW
rank. But the member gets no website invite and no indication that anything went wrong.

### Real Example — huttboles (2026-03-17)

```
23:09:55  Discord member joined: huttboles. (327970315705647124)
23:09:55  WARNING Cannot DM huttboles. — DMs closed
23:13:28  Discord role change for huttboles.: [] → ['Initiate']   ← discord_sync, not onboarding
23:22:17  self-service account request: discord_id=327970315705647124 player=219 code=K6PANAYD
23:32:00  Rank auto-corrected for player 219 (Huttboles): None → Initiate (source: discord_sync)
```

He got the Initiate role from the normal rank sync (not onboarding). He discovered `/get-account`
on his own and got his code. The flow worked, but only because he knew to look for a workaround.

### Current Self-Service Recovery Path

The `/get-account` slash command is the fallback. Any guild member can run it in any channel;
the response is ephemeral (only visible to them). It will:

1. Find or create their `discord_users` / `players` record
2. If they already have a website account → tell them to log in
3. If not → generate a 7-day invite code and show it inline

This command works regardless of DM settings because the response is ephemeral in-server.

### What's Missing (Hotfix Candidate)

There is currently **no automatic signal** to a DMs-closed member that they should run
`/get-account`. Options to consider for a future hotfix:

**Option A (chosen) — Post a message in a configurable "landing zone" channel**, @mentioning
the member and telling them to use `/get-account`. This is the planned hotfix implementation.

**Option B — Ephemeral interaction** triggered by a button in a welcome channel (requires a
persistent view registered at startup) that does the same thing as `/get-account`.

**Option C — Pin a visible note** in `#welcome` or `#rules` telling all new members to run
`/get-account` if they don't hear from the bot. This requires no code changes.

**Recommended short-term (until the hotfix ships):** Post Option C text (see Section 5 below)
in a prominent channel. Use `#landing-zone` as the interim landing channel.

---

## 3. Planned Hotfix Implementation (Option A)

> **Branch:** `hotfix/landing-zone-dm-fallback`
> **Status:** Spec only — not yet implemented

### What to build

When `discord.Forbidden` fires in `OnboardingConversation.start()`, after setting state to
`declined`, post a message in a configurable landing zone channel @mentioning the member.

**Suggested message:**
> Hey @member! It looks like I couldn't send you a DM (your Discord settings may be blocking
> messages from server members). To get set up with a **Pull All The Things website account**,
> just use `/get-account` in any channel — it only takes a second! 🎮

### Files to change

| File | Change |
|------|--------|
| `alembic/versions/0051_landing_zone_channel.py` | New migration: `ALTER TABLE common.discord_config ADD COLUMN landing_zone_channel_id VARCHAR(25)` |
| `src/sv_common/db/models.py` | Add `landing_zone_channel_id: Mapped[Optional[str]]` to `DiscordConfig` |
| `src/sv_common/guild_sync/onboarding/conversation.py` | Add `_notify_landing_zone()` helper; call it after `_set_state("declined")` in the `discord.Forbidden` handler |
| `src/guild_portal/api/admin_routes.py` | Handle `landing_zone_channel_id` in `PATCH /bot-settings`; include in response |
| `src/guild_portal/templates/admin/bot_settings.html` | New "Landing Zone Channel" card (same channel-dropdown pattern as Audit Channel); JS load/save functions |

### `_notify_landing_zone()` logic

1. Query `landing_zone_channel_id` from `common.discord_config` via `self.db_pool`
2. If null → log nothing, return silently
3. `channel = self.bot.get_channel(int(channel_id))`
4. If channel not in cache → `logger.warning(...)`, return
5. `await channel.send(f"Hey {self.member.mention}! ...")`
6. Wrap in `try/except discord.Forbidden` → `logger.warning(...)` if bot lacks send perms

### Bot Settings UI

Add a **Landing Zone Channel** card between the existing Audit Channel card and the Discord DM
Messaging card. Same pattern: grouped channel dropdown populated from `/api/guild-sync/channels`,
Save button calling `patchSettings({landing_zone_channel_id: val})`. Helper note: "If no channel
is set, members with DMs closed will receive no guidance and must find `/get-account` on their own."

---

## 4. Officer Bot Commands

All commands are slash commands (ephemeral — only the officer sees the response).

### Onboarding Management

| Command | Who | What it does |
|---------|-----|--------------|
| `/onboard-status` | Officer+ | Lists all pending onboarding sessions (not yet provisioned or resolved) |
| `/onboard-resolve @member` | Officer+ | Manually provisions a member (bypasses roster verification); sends them a DM with invite code |
| `/onboard-dismiss @member` | Officer+ | Closes a session without provisioning (useful for bot alts, non-guild members) |
| `/onboard-retry @member` | Officer+ | Re-runs roster verification for a `pending_verification` session |
| `/onboard-start @member` | Officer+ | Deletes existing session and force-starts a fresh DM conversation (requires member has DMs open) |
| `/resend-oauth @member` | Officer+ | Re-sends the Battle.net OAuth link DM to a member stuck at `oauth_pending` |

### Member Self-Service

| Command | Who | What it does |
|---------|-----|--------------|
| `/get-account` | Any member | Gets their website invite code, or confirms they already have an account |

### Guild Quotes

| Command | Who | What it does |
|---------|-----|--------------|
| `/quote` | Any member | Posts a random guild quote from a random active speaker |
| `/quote @speaker` | Any member | Posts a quote attributed to a specific person (one command per active quote subject) |

---

## 5. Onboarding Session States

| State | Meaning |
|-------|---------|
| `awaiting_dm` | Session created; DM not yet sent (or bot DMs disabled globally) |
| `asked_in_guild` | Welcome DM sent; waiting for yes/no reply |
| `asked_main` | Asked for main character name |
| `asked_alts` | Asked for alt names |
| `pending_verification` | Waiting for roster sync to confirm the character exists |
| `verified` | Character found; about to provision |
| `oauth_pending` | Provisioned; waiting for member to connect Battle.net |
| `oauth_complete` | Battle.net connected; all done |
| `provisioned` | Fully onboarded via the automated flow |
| `manually_resolved` | Closed by an officer via `/onboard-resolve` or `/onboard-dismiss` |
| `declined` | DMs were closed when the bot tried to initiate — member needs to use `/get-account` |
| `abandoned_oauth` | Member never completed OAuth after 48h; deadline checker gave up |

---

## 6. Updated Guild Quick Guide (paste into #welcome or #announcements)

> **This replaces the content previously in `docs/DISCORD.md`.**
> Paste this into a Discord channel. Discord supports **bold**, *italic*, and `code` formatting.

---

**📖 Pull All The Things — Website & Bot Quick Guide**

**🌐 The Website** — pullallthethings.com

**No login needed:**
- **Home** — Guild info, officers, recruiting status, and raid schedule
- **Roster** (`/roster`) — Full guild roster with class/spec/ilvl, composition breakdown, and Wowhead comp link
- **Crafting Corner** (`/crafting-corner`) — Browse what guild members can craft; post a guild order and the bot pings available crafters in #crafters-corner

**With a guild account:**
- **My Characters** — Your characters' progression, M+ score, WCL parse percentiles, and AH market prices
- **Vote** — When a campaign is live (art vote, polls, etc.) you'll see it on the home page
- **Availability** (Settings → Availability) — Set your weekly availability for raid planning

**To get an account:**
The bot will DM you automatically when you join — just follow the prompts.
If you don't get a DM (or have DMs from server members turned off), run **/get-account** in any channel. It'll give you a registration code right here.

---

**🤖 The Bot**

- **Auto-onboarding** — Sends you a DM when you join to get you set up with an account
- **`/get-account`** — Self-service: get your invite code or confirm your account exists
- **`/quote`** — Posts a random guild quote
- **Role sync** — Keeps your Discord rank in sync with your WoW guild rank
- **Crafting orders** — Posts an embed in #crafters-corner when someone requests a craft
- **Campaign announcements** — Posts updates when voting milestones are hit

---

**⚙️ Officers**
Full admin panel at `/admin` — player manager, raid tools, availability grid, data quality, crafting sync, attendance, WarcraftLogs, auction house pricing, and campaign management.
Officer slash commands: `/onboard-status`, `/onboard-resolve`, `/onboard-dismiss`, `/onboard-retry`, `/resend-oauth`

---

## 7. What Changed from the Old `docs/DISCORD.md`

| Old | New |
|-----|-----|
| "DM the bot and ask for an invite" | Onboarding is now automatic on join; `/get-account` is the fallback |
| No mention of My Characters page | Added — progression, M+, WCL parses, AH prices |
| No mention of officer slash commands | Full command table added |
| Availability listed as a feature | Still accurate |
| Voting/campaigns mentioned | Still accurate |
| No DMs-closed guidance | Section 2 added with the huttboles incident as the reference case |
