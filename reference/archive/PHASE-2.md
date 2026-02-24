# Phase 2: Authentication & Discord Bot

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-1 must be complete.
> **Goal:** Members can register via invite code (DM'd by the Discord bot), log in, and
> access protected routes. The bot connects to Discord, syncs roles, and sends DMs.

---

## What This Phase Produces

1. `sv_common.auth` package — JWT tokens, password hashing, invite codes
2. `sv_common.discord` package — bot client, role sync, DM dispatch
3. Registration and login API endpoints
4. Auth middleware protecting admin and member routes
5. PATT-Bot running as a background task within the FastAPI app
6. Role sync: bot reads Discord roles, updates guild_ranks.discord_role_id mapping
7. Full test coverage with Discord interactions mocked

---

## Context From Previous Phases

After Phase 1, the following exists:
- PostgreSQL with all tables, guild ranks seeded
- sv_common.identity: members, ranks, characters CRUD
- Admin API and public guild API
- FastAPI app, pytest framework, all tests passing

---

## Tasks

### 2.1 — Auth Package (`sv_common/auth/`)

**passwords.py:**
```python
def hash_password(plain: str) -> str
def verify_password(plain: str, hashed: str) -> bool
```
Use bcrypt. Straightforward.

**jwt.py:**
```python
def create_access_token(user_id: int, member_id: int, rank_level: int, expires_minutes: int = None) -> str
def decode_access_token(token: str) -> dict  # returns {"user_id": int, "member_id": int, "rank_level": int}
```
Token payload includes user_id, member_id, and rank_level so permission checks
don't require a DB lookup on every request.

**invite_codes.py:**
```python
async def generate_invite_code(db: AsyncSession, member_id: int, created_by_id: int, expires_hours: int = 72) -> str
async def validate_invite_code(db: AsyncSession, code: str) -> InviteCode | None
async def consume_invite_code(db: AsyncSession, code: str) -> InviteCode
```
Codes are 8-character alphanumeric strings (uppercase, no ambiguous chars like 0/O, 1/I/L).
Codes expire after a configurable period (default 72 hours).
A code is tied to a specific guild_member record — the member it was generated for.

### 2.2 — Auth API Endpoints (`patt/api/auth_routes.py`)

```
POST /api/v1/auth/register
    Body: { "code": "ABC12345", "discord_username": "trog", "password": "..." }
    - Validates the invite code
    - Verifies discord_username matches the member the code was generated for
    - Creates a user record, hashes password
    - Links user to guild_member
    - Consumes the invite code
    - Returns JWT token

POST /api/v1/auth/login
    Body: { "discord_username": "trog", "password": "..." }
    - Looks up member by discord_username
    - Verifies member has a linked user account
    - Checks password
    - Returns JWT token

GET  /api/v1/auth/me
    Headers: Authorization: Bearer <token>
    - Returns current user profile (member info, rank, characters)
```

### 2.3 — Auth Middleware

Create a FastAPI dependency:
```python
async def get_current_member(request: Request, db: AsyncSession) -> GuildMember:
    """Extract JWT from Authorization header, validate, return the member."""

async def require_rank(min_level: int):
    """Dependency factory — returns 403 if member rank < min_level."""
```

Usage in routes:
```python
@router.get("/api/v1/admin/members")
async def list_members(
    member: GuildMember = Depends(require_rank(4)),  # Officer+
    db: AsyncSession = Depends(get_db)
):
    ...
```

**Go back and add auth to Phase 1's admin routes:**
- All `/api/v1/admin/*` routes require Officer rank (level 4+)
- The `/api/v1/guild/*` routes remain public

### 2.4 — Discord Bot Setup (`sv_common/discord/bot.py`)

Create the PATT-Bot using discord.py with intents:
```python
import discord
from discord.ext import commands, tasks

intents = discord.Intents.default()
intents.members = True      # Required to read member list and roles
intents.message_content = False  # Not needed — bot doesn't read messages

bot = commands.Bot(command_prefix="!", intents=intents)
```

**Bot lifecycle integration with FastAPI:**

The bot runs as a background task started during FastAPI's lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the Discord bot in a background task
    asyncio.create_task(bot.start(settings.discord_bot_token))
    yield
    await bot.close()
```

**Document for Mike — Discord Developer Portal setup:**

Create a file `docs/DISCORD-BOT-SETUP.md` with step-by-step instructions:
1. Go to https://discord.com/developers/applications
2. Click "New Application" — name it "PATT-Bot"
3. Go to Bot tab → click "Add Bot"
4. Copy the token → save to `.env` as `DISCORD_BOT_TOKEN`
5. Under Privileged Gateway Intents: enable "Server Members Intent"
6. Go to OAuth2 → URL Generator:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Send Messages in Threads`, `Read Message History`, `View Channels`
7. Copy the generated URL → open in browser → invite bot to PATT Discord server
8. Copy the Discord Server ID → save to `.env` as `DISCORD_GUILD_ID`

### 2.5 — Role Sync (`sv_common/discord/role_sync.py`)

A background task that runs on a configurable interval (from discord_config table):

```python
@tasks.loop(hours=24)  # default, overridden by config
async def sync_discord_roles():
    """
    1. Fetch all members from the Discord guild
    2. For each member, check their Discord roles
    3. Look up which guild_rank maps to each Discord role (via discord_role_id)
    4. Find the highest matching rank
    5. If member's current rank differs, update it and set rank_source='discord_sync'
    6. Log any changes
    """
```

This is the core of the "Discord is the source of truth" design. When Mike promotes
someone in Discord, the next sync picks it up and updates their platform rank.

The sync also discovers new Discord members who aren't in the guild_members table yet.
It should create a new guild_member record for them with rank based on their Discord roles.

### 2.6 — DM Dispatch (`sv_common/discord/dm.py`)

```python
async def send_registration_dm(discord_id: str, invite_code: str, register_url: str) -> bool:
    """
    Send a DM to a guild member with their registration code and link.
    Returns True if sent successfully, False if DM failed (user has DMs disabled, etc.)
    """
```

The message should be friendly and on-brand:
```
Hey! You've been invited to register on the Pull All The Things guild platform.

Your registration code: **{code}**
Register here: {url}

This code expires in 72 hours. If you have any questions, ask Trog!
```

### 2.7 — Admin: Send Invite Flow

Add to admin routes:
```
POST /api/v1/admin/members/{id}/send-invite
    - Generates invite code for this member
    - Tells bot to DM the code to their discord_id
    - Returns success/failure
```

The admin page (built in Phase 4) will have a "Send Invite" button next to each
unregistered member. For now, the API endpoint is sufficient.

### 2.8 — Tests

**Unit tests:**

`test_auth.py`:
- test_hash_password_returns_different_hash_each_time
- test_verify_password_correct
- test_verify_password_incorrect
- test_create_jwt_contains_expected_claims
- test_decode_jwt_valid_token
- test_decode_jwt_expired_token_raises
- test_decode_jwt_invalid_token_raises
- test_invite_code_generation_format (8 chars, no ambiguous chars)
- test_invite_code_validation_valid
- test_invite_code_validation_expired
- test_invite_code_validation_already_used

**Integration tests:**

`test_auth_flow.py`:
- test_full_registration_flow (generate code → register → login → access /me)
- test_register_with_invalid_code_rejected
- test_register_with_expired_code_rejected
- test_register_with_wrong_username_rejected (code was for a different member)
- test_login_with_correct_credentials
- test_login_with_wrong_password_rejected
- test_login_unregistered_member_rejected
- test_protected_route_without_token_returns_401
- test_protected_route_with_insufficient_rank_returns_403
- test_admin_route_accessible_by_officer
- test_admin_route_blocked_for_member

`test_role_sync.py` (with mocked Discord API):
- test_role_sync_promotes_member_when_discord_role_added
- test_role_sync_demotes_member_when_discord_role_removed
- test_role_sync_creates_new_member_for_unknown_discord_user
- test_role_sync_skips_members_without_matching_roles
- test_role_sync_sets_source_to_discord_sync

---

## Acceptance Criteria

- [ ] User can register with a valid invite code and matching Discord username
- [ ] User can log in with Discord username + password and receive JWT
- [ ] JWT contains member_id and rank_level, enabling stateless permission checks
- [ ] Admin routes require Officer+ rank
- [ ] Invalid/expired/used invite codes are rejected
- [ ] PATT-Bot connects to Discord and appears online
- [ ] Role sync reads Discord roles and updates member ranks
- [ ] DM dispatch sends registration codes to members
- [ ] All unit and integration tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-2: authentication and discord bot"`
- [ ] Update CLAUDE.md "Current Build Status" section
