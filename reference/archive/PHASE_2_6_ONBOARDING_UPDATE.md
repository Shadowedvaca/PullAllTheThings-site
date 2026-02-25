# Phase 2.6 (Revised) — Onboarding System Update + Bot Messaging Toggle

> **Status:** Ready to execute
> **Prereqs:** Read CLAUDE.md, TESTING.md, and this file. Phase 2.5 (revised) must be complete.
> **Goal:** Update all onboarding modules to work with the Phase 2.7 player model,
> add a bot messaging kill switch to the admin UI, and wire up (but leave OFF) the
> `on_member_join` event that triggers onboarding.

---

## Background

Phase 2.6 originally built the onboarding system: a Discord DM conversation flow for
new members, auto-provisioning (Discord role + website invite + roster entries), a 24-hour
deadline checker, and officer slash commands. That code was written against the original
schema and has the same stale references as the Phase 2.5 guild_sync code.

Additionally, the `on_member_join` event was never wired up — the bot literally cannot
start onboarding conversations yet. This phase fixes the code, wires up the event, but
gates ALL bot-initiated messaging behind an admin toggle that defaults to OFF.

### What This Phase Produces

1. **Bot Messaging Toggle** — a `bot_dm_enabled` flag on `discord_config` with admin UI
2. Updated `conversation.py` — DM onboarding flow using players/discord_users
3. Updated `provisioner.py` — auto-provisioning using players/player_characters
4. Updated `deadline_checker.py` — escalation logic using new schema
5. Updated `commands.py` — officer slash commands using new schema
6. Updated `on_member_join` wiring — checks toggle before starting onboarding
7. Updated `scheduler.py` — onboarding deadline job checks toggle
8. Tests for everything

---

## The Bot Messaging Toggle

### Design

Mike wants to complete development with the bot unable to message anyone, then flip a
switch in the admin UI when he's ready. The toggle controls ALL bot-initiated DMs:
onboarding conversations, invite code delivery, provisioning confirmations. It does NOT
affect audit channel posts (those go to a server channel, not a DM).

### How it works:

1. **Database:** `common.discord_config.bot_dm_enabled` (BOOLEAN, default FALSE)
2. **Admin API:** `GET/PATCH /api/v1/admin/bot-settings` — returns/sets the flag
3. **Admin UI:** A card on the admin page with a prominent ON/OFF toggle
4. **Gate check:** A shared helper function that every DM-sending code path calls:

```python
# sv_common/discord/dm.py (add to existing module)

async def is_bot_dm_enabled(pool) -> bool:
    """Check if bot DM messaging is enabled in discord_config."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT bot_dm_enabled FROM common.discord_config LIMIT 1"
        ) or False
```

5. **Enforcement points** (every place the bot would send a DM to a user):
   - `conversation.py` → `start()` checks before sending welcome DM
   - `provisioner.py` → `_send_invite_dm()` checks before sending
   - `deadline_checker.py` → does NOT check (it posts to audit channel, not DMs)
   - `on_member_join` → checks before creating OnboardingConversation

When `bot_dm_enabled = FALSE`, the bot still:
- Syncs Discord members (discord_sync)
- Runs Blizzard API syncs
- Runs identity matching and integrity checks
- Posts to #audit-channel
- Responds to officer slash commands
- Creates onboarding sessions in the DB (state tracking works)

It just won't DM anyone. Onboarding sessions will be created in `awaiting_dm` state
and sit there until the toggle is flipped on, at which point the deadline checker's
next run will pick them up and attempt to start conversations.

---

## Task 1: Migration 0009 — Bot DM Toggle

Add the `bot_dm_enabled` column to `discord_config`.

```python
# alembic/versions/0009_bot_dm_toggle.py

def upgrade():
    op.add_column(
        "discord_config",
        sa.Column("bot_dm_enabled", sa.Boolean(), server_default="false", nullable=False),
        schema="common",
    )

def downgrade():
    op.drop_column("discord_config", "bot_dm_enabled", schema="common")
```

---

## Task 2: Update SQLAlchemy Model — DiscordConfig

**File:** `src/sv_common/db/models.py`

Add to the `DiscordConfig` model:

```python
bot_dm_enabled: Mapped[bool] = mapped_column(
    Boolean, nullable=False, server_default="false"
)
```

---

## Task 3: DM Gate Helper

**File:** `src/sv_common/discord/dm.py` (add to existing module)

Add a helper function. If `dm.py` doesn't exist yet, create it with this function
plus any existing DM dispatch code.

```python
async def is_bot_dm_enabled(pool) -> bool:
    """
    Check whether the bot is allowed to send DMs to users.

    Reads common.discord_config.bot_dm_enabled.
    Returns False if not configured or if the flag is off.
    """
    async with pool.acquire() as conn:
        enabled = await conn.fetchval(
            "SELECT bot_dm_enabled FROM common.discord_config LIMIT 1"
        )
        return bool(enabled)
```

Every DM-sending code path must call this before sending. If it returns False,
log a message like `"Bot DM disabled — skipping DM to {username}"` and return
gracefully (do NOT raise an exception).

---

## Task 4: Admin API — Bot Settings

**File:** `src/patt/api/admin_routes.py` (add to existing admin routes)

```python
@router.get("/bot-settings")
async def get_bot_settings(
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(require_rank(4)),  # Officer+
):
    """Get current bot configuration."""
    config = await session.execute(
        select(DiscordConfig).limit(1)
    )
    row = config.scalar_one_or_none()
    return {
        "ok": True,
        "data": {
            "bot_dm_enabled": row.bot_dm_enabled if row else False,
            "role_sync_interval_hours": row.role_sync_interval_hours if row else 24,
            "guild_discord_id": row.guild_discord_id if row else None,
        }
    }


@router.patch("/bot-settings")
async def update_bot_settings(
    payload: dict,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(require_rank(4)),  # Officer+
):
    """Update bot configuration. Currently supports: bot_dm_enabled."""
    config = await session.execute(
        select(DiscordConfig).limit(1)
    )
    row = config.scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "No discord_config row found"}

    if "bot_dm_enabled" in payload:
        row.bot_dm_enabled = bool(payload["bot_dm_enabled"])

    await session.commit()

    # Log the change
    logger.info(
        "Bot settings updated by %s: bot_dm_enabled=%s",
        player.display_name, row.bot_dm_enabled
    )

    return {
        "ok": True,
        "data": {
            "bot_dm_enabled": row.bot_dm_enabled,
        }
    }
```

---

## Task 5: Admin UI — Bot Settings Card

**File:** `src/patt/templates/admin/bot_settings.html` (new partial, or add to existing admin page)

Add a card to the admin dashboard that shows the bot DM toggle. Use the same dark
fantasy tavern aesthetic as the rest of the admin pages.

### Design:

```
┌─────────────────────────────────────────┐
│  🤖 Bot Messaging                       │
│                                         │
│  Discord DM Onboarding                  │
│  ┌─────┐                                │
│  │ OFF │  ← Toggle switch               │
│  └─────┘                                │
│                                         │
│  When OFF, the bot will not send any    │
│  DMs to guild members. Syncing and      │
│  audit channel posts still work.        │
│                                         │
│  Status: 3 sessions awaiting DM         │
│          1 session pending verification │
└─────────────────────────────────────────┘
```

The toggle calls `PATCH /api/v1/admin/bot-settings` with `{"bot_dm_enabled": true/false}`.

Include a live count of pending onboarding sessions so Mike can see the backlog before
flipping the switch. Query from the API:

```python
@router.get("/onboarding-stats")
async def get_onboarding_stats(
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(require_rank(4)),
):
    """Get counts of onboarding sessions by state."""
    result = await session.execute(
        text("""
            SELECT state, COUNT(*) as count
            FROM guild_identity.onboarding_sessions
            WHERE state NOT IN ('provisioned', 'manually_resolved', 'declined')
            GROUP BY state
        """)
    )
    stats = {row.state: row.count for row in result}
    return {"ok": True, "data": stats}
```

### JavaScript for the toggle:

```javascript
async function toggleBotDm(enabled) {
    const resp = await fetch('/api/v1/admin/bot-settings', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bot_dm_enabled: enabled})
    });
    const data = await resp.json();
    if (data.ok) {
        updateToggleUI(data.data.bot_dm_enabled);
    }
}
```

Use a CSS toggle switch styled with the gold (#d4a84b) accent when ON, muted gray
when OFF. The switch should be large and obvious — this is a "big red button" feature.

---

## Task 6: Update `conversation.py`

**File:** `src/sv_common/guild_sync/onboarding/conversation.py`

### Schema renames:

| Old reference | New reference |
|---|---|
| `guild_identity.discord_members` | `guild_identity.discord_users` |
| `guild_identity.persons` | `guild_identity.players` |
| `discord_members.person_id` | `players.discord_user_id` |
| `wow_characters.person_id` | (use player_characters bridge) |
| `verified_person_id` (on onboarding_sessions) | `verified_player_id` (already renamed in 2.7 migration) |

### `start()` method:

**Add DM gate check at the very top:**
```python
async def start(self):
    from sv_common.discord.dm import is_bot_dm_enabled
    if not await is_bot_dm_enabled(self.db_pool):
        logger.info("Bot DM disabled — skipping onboarding DM for %s", self.member.name)
        # Still create the session so we track that this person joined
        await self._create_session_only()
        return
    # ... rest of existing start logic
```

**`_create_session_only()`** — creates the onboarding session in `awaiting_dm` state
without attempting to send a DM. When DMs are enabled later, the deadline checker will
find these sessions and attempt to start conversations.

**Update session creation SQL:**
```python
# Old: SELECT id FROM guild_identity.discord_members WHERE discord_id = $1
# New: SELECT id FROM guild_identity.discord_users WHERE discord_id = $1

# Old: INSERT INTO guild_identity.onboarding_sessions (discord_member_id, ...)
# New: INSERT INTO guild_identity.onboarding_sessions (discord_member_id, ...)
#   NOTE: The column on onboarding_sessions is still called discord_member_id
#   (it's just a column name — the FK target was updated to discord_users in 0007)
#   Keep using discord_member_id as the column name, just pass discord_users.id as value
```

### `_attempt_verification()` method:

**Old logic:** Finds matching wow_characters, sets `person_id` on characters, creates
person, links discord_member to person.

**New logic:**
1. Find matching character(s) in `wow_characters` by reported name
2. Check if a player already owns that character (via `player_characters`)
   - YES → link this discord user to that player (`players.discord_user_id = du.id`)
   - NO → Create new player, insert player_characters entry
3. Update onboarding session: `verified_player_id = player.id`

```python
async def _attempt_verification(self):
    session = await self._get_session()
    if not session["reported_main_name"]:
        return False

    async with self.db_pool.acquire() as conn:
        # Find the character
        char = await conn.fetchrow(
            """SELECT wc.id, wc.character_name, wc.realm_slug
               FROM guild_identity.wow_characters wc
               WHERE LOWER(wc.character_name) = $1
                 AND wc.removed_at IS NULL""",
            session["reported_main_name"].lower()
        )
        if not char:
            # Increment attempt counter
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    verification_attempts = verification_attempts + 1,
                    last_verification_at = NOW(), updated_at = NOW()
                   WHERE id = $1""",
                self.session_id
            )
            return False

        # Check if character already belongs to a player
        existing_pc = await conn.fetchrow(
            """SELECT pc.player_id FROM guild_identity.player_characters pc
               WHERE pc.character_id = $1""",
            char["id"]
        )

        # Get discord_users.id for this member
        du_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
            str(self.member.id)
        )
        du_id = du_row["id"] if du_row else None

        if existing_pc:
            player_id = existing_pc["player_id"]
            # Link discord to existing player if not already linked
            if du_id:
                await conn.execute(
                    """UPDATE guild_identity.players SET discord_user_id = $1, updated_at = NOW()
                       WHERE id = $2 AND discord_user_id IS NULL""",
                    du_id, player_id
                )
        else:
            # Create new player
            display = self.member.nick or self.member.display_name
            player_id = await conn.fetchval(
                """INSERT INTO guild_identity.players (display_name, discord_user_id)
                   VALUES ($1, $2) RETURNING id""",
                display, du_id
            )
            # Link character to player
            await conn.execute(
                """INSERT INTO guild_identity.player_characters (player_id, character_id)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                player_id, char["id"]
            )

        # Link reported alts
        for alt_name in (session["reported_alt_names"] or []):
            alt_char = await conn.fetchrow(
                """SELECT id FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1
                     AND removed_at IS NULL
                     AND id NOT IN (SELECT character_id FROM guild_identity.player_characters)""",
                alt_name.lower()
            )
            if alt_char:
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters (player_id, character_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    player_id, alt_char["id"]
                )

        # Update session
        await conn.execute(
            """UPDATE guild_identity.onboarding_sessions SET
                state = 'verified',
                verified_at = NOW(),
                verified_player_id = $2,
                verification_attempts = verification_attempts + 1,
                last_verification_at = NOW(),
                updated_at = NOW()
               WHERE id = $1""",
            self.session_id, player_id
        )

    # Auto-provision
    await self._auto_provision(player_id)
    return True
```

### `_auto_provision()` method:
```python
async def _auto_provision(self, player_id: int):
    from .provisioner import AutoProvisioner
    provisioner = AutoProvisioner(self.db_pool, self.bot)
    result = await provisioner.provision_player(
        player_id,
        silent=False,
        onboarding_session_id=self.session_id,
    )
    # Update session with provisioning results
    async with self.db_pool.acquire() as conn:
        await conn.execute(
            """UPDATE guild_identity.onboarding_sessions SET
                state = 'provisioned',
                website_invite_sent = $2,
                website_invite_code = $3,
                roster_entries_created = $4,
                discord_role_assigned = $5,
                completed_at = NOW(), updated_at = NOW()
               WHERE id = $1""",
            self.session_id,
            result["invite_code"] is not None,
            result["invite_code"],
            result["characters_linked"] > 0,
            result["discord_role_assigned"],
        )
```

### `_find_char()` helper:
```python
# Old: SELECT ... FROM wow_characters WHERE LOWER(character_name) = $1 AND removed_at IS NULL
# New: Same query, but remove any reference to realm_name if it uses the old column
#      Use realm_slug instead. Also remove character_class (use class_id join if needed)
async def _find_char(self, name: str) -> Optional[dict]:
    async with self.db_pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT wc.id, wc.character_name, wc.realm_slug,
                      c.name as class_name
               FROM guild_identity.wow_characters wc
               LEFT JOIN guild_identity.classes c ON c.id = wc.class_id
               WHERE LOWER(wc.character_name) = $1 AND wc.removed_at IS NULL""",
            name.lower(),
        )
```

---

## Task 7: Update `provisioner.py`

**File:** `src/sv_common/guild_sync/onboarding/provisioner.py`

### Complete rewrite — the old provisioner worked with guild_members + characters tables

The provisioner's job is simpler now because the player model already tracks everything.
No need to "find or create guild_member" or "sync characters to common.characters" —
those tables are gone.

**Rename core method:** `provision_person()` → `provision_player()`

### New `provision_player()` logic:

```python
async def provision_player(
    self,
    player_id: int,
    silent: bool = False,
    onboarding_session_id: Optional[int] = None,
) -> dict:
    result = {
        "player_id": player_id,
        "discord_role_assigned": False,
        "invite_code": None,
        "characters_linked": 0,
        "errors": [],
    }

    async with self.db_pool.acquire() as conn:
        # Get player with discord info
        player = await conn.fetchrow(
            """SELECT p.id, p.display_name, p.discord_user_id,
                      du.discord_id
               FROM guild_identity.players p
               LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE p.id = $1""",
            player_id
        )
        if not player:
            result["errors"].append("Player not found")
            return result

        discord_id = player["discord_id"]

        # Count linked characters
        char_count = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.player_characters WHERE player_id = $1",
            player_id
        )
        result["characters_linked"] = char_count

        # Get highest rank from linked characters
        rank_row = await conn.fetchrow(
            """SELECT gr.name as rank_name
               FROM guild_identity.player_characters pc
               JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
               JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
               WHERE pc.player_id = $1 AND wc.removed_at IS NULL
               ORDER BY gr.level DESC LIMIT 1""",
            player_id
        )
        rank_name = rank_row["rank_name"] if rank_row else "Initiate"

    # Assign Discord role (requires live bot, skipped in silent mode)
    if not silent and self.bot and discord_id:
        result["discord_role_assigned"] = await self._assign_discord_role(
            discord_id, rank_name
        )

    # Generate invite + send DM (skipped in silent mode, also checks DM gate)
    if not silent and discord_id:
        invite_code = await self._create_invite(player_id, onboarding_session_id)
        result["invite_code"] = invite_code
        if invite_code and self.bot:
            await self._send_invite_dm(discord_id, invite_code)

    return result
```

### `_assign_discord_role()` — no schema changes, just uses Discord API:
- Find the guild member by discord_id
- Map rank_name to Discord role name via RANK_TO_DISCORD_ROLE dict
- Add role to member
- No changes needed except ensuring the rank_name comes from guild_ranks.name (not the old text field)

### `_create_invite()` — update to use player_id:
```python
# Old: INSERT INTO common.invite_codes (player_id, created_by_player_id, ...)
#      Used guild_member_id
# New: Uses player_id directly (FK already points to players since 2.7)
async def _create_invite(self, player_id, onboarding_session_id=None):
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    async with self.db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO common.invite_codes
               (code, player_id, generated_by, onboarding_session_id, expires_at)
               VALUES ($1, $2, 'auto_onboarding', $3, NOW() + interval '7 days')""",
            code, player_id, onboarding_session_id
        )
    return code
```

### `_send_invite_dm()` — add DM gate check:
```python
async def _send_invite_dm(self, discord_id, invite_code):
    from sv_common.discord.dm import is_bot_dm_enabled
    if not await is_bot_dm_enabled(self.db_pool):
        logger.info("Bot DM disabled — invite code %s created but not sent", invite_code)
        return
    # ... existing DM sending logic (find user, send embed)
```

### Eliminated code:
- `_find_or_create_guild_member()` — gone, guild_members table doesn't exist
- `_sync_characters()` — gone, common.characters table doesn't exist
- `_sync_rank()` — gone, rank lives on player.guild_rank_id (set by identity engine)
- Any reference to `common.guild_members` or `common.characters`

---

## Task 8: Update `deadline_checker.py`

**File:** `src/sv_common/guild_sync/onboarding/deadline_checker.py`

### Schema renames in SQL queries:
- `guild_identity.discord_members` → `guild_identity.discord_users`
- `dm.username, dm.display_name` → `du.username, du.display_name` (alias change for clarity)
- `verified_person_id` → `verified_player_id` (column renamed in 2.7)

### Add DM-awareness for stalled sessions:

When `bot_dm_enabled` goes from FALSE → TRUE, there may be sessions stuck in
`awaiting_dm` state. The deadline checker should detect these and attempt to start
conversations:

```python
async def check_pending(self):
    """Check for sessions that need attention."""
    # 1. Retry verification for pending_verification sessions (existing logic)
    await self._retry_verifications()

    # 2. Check deadlines and escalate (existing logic)
    await self._check_deadlines()

    # 3. NEW: Attempt to start conversations for awaiting_dm sessions
    #    (only runs if bot DM is enabled)
    await self._resume_awaiting_dm_sessions()

async def _resume_awaiting_dm_sessions(self):
    """If DMs are now enabled, start conversations for sessions stuck in awaiting_dm."""
    from sv_common.discord.dm import is_bot_dm_enabled
    if not await is_bot_dm_enabled(self.db_pool):
        return  # Still disabled, skip

    async with self.db_pool.acquire() as conn:
        awaiting = await conn.fetch(
            """SELECT id, discord_id FROM guild_identity.onboarding_sessions
               WHERE state = 'awaiting_dm' AND dm_sent_at IS NULL
               ORDER BY created_at ASC LIMIT 10"""
        )

    for session in awaiting:
        member = await self._find_discord_member(session["discord_id"])
        if not member:
            continue
        from .conversation import OnboardingConversation
        conv = OnboardingConversation(self.bot, member, self.db_pool)
        conv.session_id = session["id"]
        try:
            await conv._send_welcome()
        except Exception as e:
            logger.warning("Failed to resume DM for %s: %s", session["discord_id"], e)
```

### `_retry_verifications()`:
Update the verification retry logic to use the same pattern as the updated
`conversation.py._attempt_verification()` — go through `player_characters` bridge,
not `person_id` on characters.

### `_escalate()`:
Update SQL: `discord_members` → `discord_users`, `verified_person_id` → `verified_player_id`

### `_provision()`:
```python
# Old: AutoProvisioner(self.db_pool, self.bot).provision_person(person_id, ...)
# New: AutoProvisioner(self.db_pool, self.bot).provision_player(player_id, ...)
```

---

## Task 9: Update `commands.py`

**File:** `src/sv_common/guild_sync/onboarding/commands.py`

### `/onboard-status`:
```python
# Old query selected from onboarding_sessions, no changes to columns
# But update: verified_person_id → verified_player_id in any display
```

### `/onboard-resolve <user>`:
```python
# Old: Looked up discord_members.person_id, created persons record
# New:
dm_row = await conn.fetchrow(
    "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
    str(member.id)
)
# Check if player exists for this discord user
player_row = await conn.fetchrow(
    "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
    dm_row["id"]
)
if not player_row:
    # Create a bare player
    player_id = await conn.fetchval(
        "INSERT INTO guild_identity.players (display_name, discord_user_id) VALUES ($1, $2) RETURNING id",
        member.display_name, dm_row["id"]
    )
else:
    player_id = player_row["id"]

# Update session
await conn.execute(
    """UPDATE guild_identity.onboarding_sessions SET
        state = 'verified', verified_at = NOW(), verified_player_id = $2, updated_at = NOW()
       WHERE id = $1""",
    session["id"], player_id
)

# Provision
provisioner = AutoProvisioner(db_pool, interaction.client)
result = await provisioner.provision_player(player_id, silent=False, onboarding_session_id=session["id"])
```

### `/onboard-dismiss <user>`:
No schema changes — just updates session state.

### `/onboard-retry <user>`:
Use updated verification logic (player_characters bridge, not person_id).

---

## Task 10: Wire Up `on_member_join`

**File:** `src/sv_common/discord/bot.py` (or wherever bot events are registered)

This is the activation switch. Add the event handler but gate it behind the toggle:

```python
@bot.event
async def on_member_join(member):
    if member.bot:
        return

    # Phase 2.5: Record the new member in discord_users
    from sv_common.guild_sync.discord_sync import on_member_join as sync_member_join
    await sync_member_join(db_pool, member)

    # Phase 2.6: Start onboarding (gated by bot_dm_enabled)
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation
    conv = OnboardingConversation(bot, member, db_pool)
    asyncio.create_task(conv.start())
    # conv.start() will check bot_dm_enabled internally and either
    # send a DM or just create a session in awaiting_dm state
```

Also wire up the existing events if not already done:

```python
@bot.event
async def on_member_remove(member):
    if member.bot:
        return
    from sv_common.guild_sync.discord_sync import on_member_remove as sync_member_remove
    await sync_member_remove(db_pool, member)

@bot.event
async def on_member_update(before, after):
    if after.bot:
        return
    from sv_common.guild_sync.discord_sync import on_member_update as sync_member_update
    await sync_member_update(db_pool, before, after)
```

---

## Task 11: Update Scheduler — Onboarding Job

**File:** `src/sv_common/guild_sync/scheduler.py`

Re-enable the `run_onboarding_check` job (which was stubbed out in Phase 2.5 revised):

```python
from .onboarding.deadline_checker import OnboardingDeadlineChecker

async def run_onboarding_check(self):
    """Run onboarding deadline checks and resume stalled sessions."""
    checker = OnboardingDeadlineChecker(self.bot, self.db_pool, self.audit_channel_id)
    await checker.check_pending()
```

Add the scheduler job back in `start()`:

```python
self.scheduler.add_job(
    self.run_onboarding_check,
    IntervalTrigger(minutes=30),
    id="onboarding_check",
    name="Onboarding Deadline & Verification Check",
    misfire_grace_time=300,
)
```

Also add to `run_blizzard_sync()` — after a Blizzard roster sync, retry onboarding
verifications (new roster data may unlock matches):

```python
async def run_blizzard_sync(self):
    # ... existing sync, match, integrity check ...

    # Step 5: Retry onboarding verifications
    await self.run_onboarding_check()
```

---

## Task 12: Register Slash Commands

Wire up the `/onboard-*` slash commands in the bot startup:

```python
# In bot startup / on_ready:
from sv_common.guild_sync.onboarding.commands import register_onboarding_commands
register_onboarding_commands(bot.tree, db_pool, audit_channel_id)
await bot.tree.sync()
```

If the commands are already registered but not functional due to stale imports, the
code updates in Task 9 will fix them.

---

## Task 13: Tests

### New tests for bot messaging toggle:

**`test_bot_dm_gate`:**
- Test `is_bot_dm_enabled()` returns False when flag is False
- Test returns True when flag is True
- Test returns False when no discord_config row exists

**`test_admin_bot_settings_api`:**
- `GET /api/v1/admin/bot-settings` returns current state
- `PATCH /api/v1/admin/bot-settings` toggles the flag
- Non-officer gets 403

**`test_onboarding_respects_dm_gate`:**
- When `bot_dm_enabled = False`:
  - `conversation.start()` creates session in `awaiting_dm` but does NOT send DM
  - `provisioner._send_invite_dm()` creates invite code but does NOT send DM
- When `bot_dm_enabled = True`:
  - Normal flow proceeds

### Updated onboarding tests:

**conversation tests:**
- Test welcome DM sends correctly (with DM enabled)
- Test "yes" path: in guild → reports main → reports alts → verification
- Test "no" path: not in guild yet → await
- Test verification creates player and player_characters entries
- Test verification links to existing player when character already owned
- Test alt linking via reported names
- Test timeout saves state

**provisioner tests:**
- Test `provision_player()` assigns Discord role
- Test role defaults to Initiate when no rank info
- Test invite code generation (format, uniqueness, expiry)
- Test idempotency (provision twice doesn't duplicate)
- Test silent mode skips DMs and role assignment

**deadline_checker tests:**
- Test escalation fires after 24 hours
- Test re-verification succeeds when character appears in roster
- Test `_resume_awaiting_dm_sessions()` starts conversations when DMs enabled
- Test `_resume_awaiting_dm_sessions()` skips when DMs disabled
- Test no double-escalation

**commands tests:**
- Test `/onboard-status` lists pending sessions
- Test `/onboard-resolve` creates player and provisions
- Test `/onboard-dismiss` closes session
- Test officer role required for all commands

### Smoke test update:
Update `tests/unit/test_smoke.py`:
- Import `OnboardingSession` model if it exists in models.py
- Verify DiscordConfig has `bot_dm_enabled` field

---

## Acceptance Criteria

- [ ] Migration 0009 adds `bot_dm_enabled` column to `discord_config`
- [ ] DiscordConfig model has `bot_dm_enabled` field
- [ ] `is_bot_dm_enabled()` helper works correctly
- [ ] Admin API: GET/PATCH `/api/v1/admin/bot-settings` works
- [ ] Admin UI: Bot settings card with ON/OFF toggle, pending session counts
- [ ] `conversation.py` creates sessions and sends DMs using player model (when enabled)
- [ ] `conversation.py` creates sessions WITHOUT sending DMs (when disabled)
- [ ] `provisioner.py` provisions using player model, checks DM gate for invite delivery
- [ ] `deadline_checker.py` resumes stalled sessions when DMs are enabled
- [ ] `commands.py` slash commands work with player model
- [ ] `on_member_join` event wired up, calls discord_sync then starts onboarding
- [ ] `on_member_remove` and `on_member_update` events wired up
- [ ] Scheduler runs onboarding deadline check every 30 minutes
- [ ] **`bot_dm_enabled` defaults to FALSE** — no DMs sent until Mike flips the switch
- [ ] No references to `persons`, `discord_members` (table), `identity_links`,
      `person_id`, `guild_members`, or `characters` remain in any onboarding module
- [ ] All onboarding tests pass
- [ ] All existing tests still pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-2.6: onboarding updated for player model + bot DM toggle"`
- [ ] Update CLAUDE.md:
  - Mark onboarding as "updated and ready, DM toggle defaults to OFF"
  - Remove from dormant code list
  - Add `bot_dm_enabled` to discord_config schema docs
  - Add migration 0009 to list
- [ ] Update MEMORY.md with completion note
- [ ] Tell Mike: "Bot DM toggle is OFF. Go to Admin → Bot Settings to enable when ready."
