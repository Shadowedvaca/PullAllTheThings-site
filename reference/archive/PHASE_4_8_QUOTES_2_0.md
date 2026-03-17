# Phase 4.8 — Quotes 2.0: Player-Associated Quotes

> **Status:** Planning
> **Migration:** 0035
> **Depends on:** Phase 4.3 complete (migration 0034)

---

## Goal

Evolve the guild quotes system from a single anonymous pool into a per-player system where each guild member can have their own curated list of quotes and titles. Each person gets their own Discord slash command (e.g., `/mito`, `/charger`). Admins can manage any player's quotes and police abuse.

### Before (current)
```
Pull All The Things, The Unkillable
"Avoidable damage? I'm sorry, is this some sort of peasant joke I'm too Paladin to understand?"
Pull All The Things
```
→ Generic guild name as author, random title drawn from a shared pool, no attribution.

### After
```
Mito, Charger Enthusiast
"Raiding is like a sewer. What you get out of it largely depends on what you put into it."
Pull All The Things • Sen'jin
```
→ Specific player's name as author, title from that player's own pool, guild + realm in footer.

---

## Current State

| Component | Location | Notes |
|-----------|----------|-------|
| Models | `src/sv_common/db/models.py` | `GuildQuote`, `GuildQuoteTitle` — no player FK |
| Bot command | `src/guild_portal/bot/guild_quote_commands.py` | Single `/quote`, registered at startup |
| CRUD UI | `src/guild_portal/static/legacy/mitos-corner.html` | Legacy static HTML, public-facing |
| REST API | `src/guild_portal/api/guild_routes.py` | No auth; global pool only |
| Front page | `public_pages.py` + `index.html` | Random quote + title from global pool |

**Key constraint:** Discord slash commands cannot be registered dynamically per-request. All commands must be registered at bot startup (or via an explicit admin "sync commands" action that restarts/re-syncs the command tree). This is a Discord API limitation.

---

## Data Model (migration 0035)

### New table: `patt.quote_subjects`

Represents a guild member who has been granted a personal quote collection.

```sql
CREATE TABLE patt.quote_subjects (
    id          SERIAL PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    command_slug VARCHAR(32) NOT NULL UNIQUE,  -- used as the Discord slash command name (e.g. "mito")
    display_name VARCHAR(100) NOT NULL,         -- shown in Discord embed (e.g. "Mito")
    active      BOOLEAN NOT NULL DEFAULT TRUE,  -- FALSE = command not registered, quotes hidden
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT quote_subjects_slug_format CHECK (command_slug ~ '^[a-z][a-z0-9_-]{0,30}$')
);

CREATE UNIQUE INDEX quote_subjects_player_id_idx ON patt.quote_subjects(player_id);
```

**Design notes:**
- `command_slug` is the Discord command name. Must be lowercase letters/numbers/hyphens (Discord requirement). Max 32 chars.
- `display_name` is freeform — e.g., `"Mito"` or `"Rocket"`. Appears as the embed author name.
- `active=FALSE` lets an admin disable a subject's command without deleting their quotes.
- One player can only appear once (unique index on `player_id`).

### Modify: `patt.guild_quotes`

Add `subject_id` FK (nullable for backward-compat during migration).

```sql
ALTER TABLE patt.guild_quotes
    ADD COLUMN subject_id INTEGER REFERENCES patt.quote_subjects(id) ON DELETE CASCADE;

-- After data migration, make non-nullable and add index
CREATE INDEX guild_quotes_subject_id_idx ON patt.guild_quotes(subject_id);
```

### Modify: `patt.guild_quote_titles`

Same change as guild_quotes.

```sql
ALTER TABLE patt.guild_quote_titles
    ADD COLUMN subject_id INTEGER REFERENCES patt.quote_subjects(id) ON DELETE CASCADE;

CREATE INDEX guild_quote_titles_subject_id_idx ON patt.guild_quote_titles(subject_id);
```

### Migration strategy for existing data

Existing quotes/titles in the DB (the original Mito content) will either:
1. Be left with `subject_id = NULL` and treated as a legacy "unassigned" pool, OR
2. A specific `quote_subjects` row is created for Mito and existing quotes migrated to it.

The migration will create one hardcoded subject row for the existing data if `guild_quotes` is non-empty. If the table is empty (new deployments), nothing extra happens.

> **Decision for PATT:** The existing quotes belong to Mito. The migration will insert a `quote_subjects` row with `command_slug='mito'` and `display_name='Mito'` linked to the Mito player record (found by Discord display name), then set `subject_id` on all existing quotes/titles to that row. If no matching player is found, leave `subject_id=NULL` and prompt the admin to assign via the UI.

---

## ORM Models

### New model: `QuoteSubject`

```python
class QuoteSubject(Base):
    __tablename__ = "quote_subjects"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("guild_identity.players.id", ondelete="CASCADE"), nullable=False, unique=True)
    command_slug: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    player: Mapped["Player"] = relationship("Player", foreign_keys=[player_id])
    quotes: Mapped[list["GuildQuote"]] = relationship("GuildQuote", back_populates="subject", cascade="all, delete-orphan")
    titles: Mapped[list["GuildQuoteTitle"]] = relationship("GuildQuoteTitle", back_populates="subject", cascade="all, delete-orphan")
```

### Updated `GuildQuote` and `GuildQuoteTitle`

Add `subject_id` FK and relationship back to `QuoteSubject`.

---

## Bot Command Architecture

### Current approach (broken for 2.0)
A single static `/quote` command registered at startup. Doesn't support per-player commands.

### New approach: Dynamic command registration at startup

At bot startup (`on_ready`), query `patt.quote_subjects WHERE active=TRUE` and register one slash command per row. Also keep a single `/quote` command that picks a random active subject and draws from their pool.

```
/quote          → picks random active subject → random quote from their pool
/mito           → always pulls from Mito's pool
/rocket         → always pulls from Rocket's pool
/charger        → etc.
```

**Registration flow:**

```python
async def register_quote_commands(tree, db_pool):
    if not is_guild_quotes_enabled():
        return

    async with db_pool.acquire() as conn:
        subjects = await conn.fetch(
            "SELECT id, command_slug, display_name FROM patt.quote_subjects WHERE active=TRUE"
        )

    # Register /quote (random subject)
    @tree.command(name="quote", description="Hear a random guild quote")
    async def random_quote(interaction): ...

    # Register one command per active subject
    for subject in subjects:
        _register_subject_command(tree, db_pool, subject)
```

**Re-sync requirement:** When an admin adds/removes/activates/deactivates a subject, the Discord command tree must be re-synced. This is done via an explicit **"Sync Bot Commands"** button in the admin UI which calls a new API endpoint. The endpoint triggers `await tree.sync(guild=...)` on the live bot instance.

> **Note:** Discord rate-limits global command syncs. For a guild-scoped bot (which PATT is), guild syncs are instant and not rate-limited. Always sync to the specific guild, not globally.

**Architecture for passing bot handle to admin API:**
The bot module exposes a `get_bot()` function already (`bot.py` line 161). The admin endpoint will call `get_bot()` and invoke a new `sync_quote_commands()` helper that:
1. Removes all existing subject slash commands from the tree (to avoid duplicates)
2. Calls `register_guild_quote_commands(bot.tree, db_pool)` again to re-register with fresh DB data
3. Calls `bot.tree.copy_global_to(guild=discord_guild)` then `await bot.tree.sync(guild=discord_guild)` — the exact same pattern already used in `on_ready()` (bot.py lines 52–53)

**Confirmed:** Guild-scoped sync works on the live bot without a restart. `bot.py` already uses `await bot.tree.sync(guild=discord_guild)` in `on_ready`, so the live-sync pattern is established and will work identically from the admin endpoint.

---

## API Changes

### New endpoints

```
# Quote Subjects (admin-only)
GET    /api/v1/admin/quote-subjects                → list all subjects with stats
POST   /api/v1/admin/quote-subjects                → create subject (assign player)
PATCH  /api/v1/admin/quote-subjects/{id}           → update display_name, command_slug, active
DELETE /api/v1/admin/quote-subjects/{id}           → delete subject + all their quotes/titles
POST   /api/v1/admin/quote-subjects/sync-commands  → trigger Discord command tree re-sync

# Quotes (admin-only)
GET    /api/v1/admin/quote-subjects/{id}/quotes    → list quotes for a subject
POST   /api/v1/admin/quote-subjects/{id}/quotes    → add quote for a subject
PUT    /api/v1/admin/quotes/{quote_id}             → update quote (any subject)
DELETE /api/v1/admin/quotes/{quote_id}             → delete quote (any subject)

# Titles (admin-only)
GET    /api/v1/admin/quote-subjects/{id}/titles    → list titles for a subject
POST   /api/v1/admin/quote-subjects/{id}/titles    → add title for a subject
PUT    /api/v1/admin/titles/{title_id}             → update title (any subject)
DELETE /api/v1/admin/titles/{title_id}             → delete title (any subject)
```

**Auth:** All new endpoints require Officer+ rank (same as other admin APIs). The public read endpoint (`GET /api/v1/guild/quotes`) will be updated to accept an optional `?subject={slug}` filter for the preview tab.

### Deprecate public CRUD

The existing unauthenticated `POST/PUT/DELETE /api/v1/guild/quotes` and `/api/v1/guild/quote-titles` endpoints will be **removed** (or return 410 Gone). The legacy `mitos-corner.html` page will be **replaced** with a proper admin-managed UI.

---

## Admin UI

### Route: `/admin/quotes`

New admin page (extending `base_admin.html`). Accessible to Officers+.

**Layout: Two-panel**

**Left panel — Subject list**
- Lists all players assigned as quote subjects
- Each row: display name, command slug, quote count, title count, active toggle
- "Add Person" button → modal to search for a player and assign them
- "Sync Bot Commands" button → calls sync endpoint, shows success/error toast
- Click a row to load their quotes in the right panel

**Right panel — Quote/title editor for selected subject**
- Two tabs: Quotes | Titles
- List of existing quotes with inline edit + delete (trashcan)
- "Add Quote" textarea + save button
- "Add Title" input + save button
- Admin note: "You are editing [Player Name]'s quotes. All changes are immediate."

**Add Subject modal:**
- Player search (text input that autocompletes against `guild_identity.players` — same pattern as Player Manager)
- Display name field (defaults to player's Discord username)
- Command slug field (auto-generated from display name, admin can override)
- Slug validation: lowercase, no spaces, no special chars, shows live preview: `/{slug}`
- Submit → creates `quote_subjects` row

**Admin safeguards:**
- Deleting a subject shows a confirmation: "This will permanently delete all N quotes and N titles for [Name]. This cannot be undone."
- Deactivating (active toggle OFF) hides command from Discord and from front page, but keeps data
- After any subject add/remove/toggle, banner appears: "Bot commands are out of sync. Click 'Sync Bot Commands' to apply changes to Discord."

### Remove legacy `mitos-corner.html`

The file `src/guild_portal/static/legacy/mitos-corner.html` will be replaced with a redirect to `/admin/quotes` (or a 410 if the person isn't logged in). The URL `/mitos-corner.html` was always a stopgap.

---

## Discord Embed Format (updated)

```
Author: {display_name}, {random title from subject's pool}
Description (italics): "{quote text}"
Footer: {guild_name} • {realm_display_name}
Color: site accent color
```

If a subject has no titles, the author line is just `{display_name}`. Realm is pulled from `config_cache.get_realm_display_name()`.

---

## Front Page Display (updated)

The "Guild Quote of the Day" section on `/` will:
1. Pick a random **active** subject
2. Pick a random quote + title from their pool
3. Display as: `— {display_name}, {title}` attribution line below the quote

---

## File Changes Summary

| File | Change |
|------|--------|
| `src/sv_common/db/models.py` | Add `QuoteSubject`; add `subject_id` FK + relationship to `GuildQuote` and `GuildQuoteTitle` |
| `alembic/versions/0035_*.py` | New migration: `quote_subjects` table, `subject_id` columns on quotes/titles, data migration |
| `src/guild_portal/bot/guild_quote_commands.py` | Rewrite to register per-subject commands dynamically; add `sync_commands()` helper |
| `src/sv_common/discord/bot.py` | Expose `sync_commands()` method on bot class; wire to `app.state.bot` |
| `src/guild_portal/api/guild_routes.py` | Remove public CRUD endpoints; update `GET /quotes` to support `?subject=` filter |
| `src/guild_portal/api/admin_routes.py` | Add quote subjects + quotes + titles admin endpoints |
| `src/guild_portal/pages/admin_pages.py` | Add `/admin/quotes` route |
| `src/guild_portal/templates/admin/quotes.html` | New: two-panel admin UI |
| `src/guild_portal/pages/public_pages.py` | Update index route to load quote with subject attribution |
| `src/guild_portal/templates/public/index.html` | Update quote display to show player attribution |
| `src/guild_portal/static/legacy/mitos-corner.html` | Replace with redirect/410 |
| `src/guild_portal/static/js/quotes.js` | New: admin quotes page JS (subject list, editor, sync button) |
| `src/guild_portal/static/css/quotes.css` | New: quotes admin page styles (or extend main.css) |
| `tests/unit/test_quotes.py` | New: unit tests for slug validation, command registration logic |
| `tests/integration/test_quotes_api.py` | New: integration tests for all new admin endpoints |

---

## Out of Scope

- Player self-service (players submitting their own quotes without admin approval) — keep it admin-managed for now
- Quote moderation queue / approval flow — admin adds directly, no workflow
- Public "browse all quotes by person" page — front page random display is sufficient
- Rate limiting the Discord commands — Discord handles this natively

---

## Open Questions

1. **Command slug conflict:** What if the desired slug is a reserved Discord command name? Add a validation list of known reserved names.
2. ~~**Bot restart required?**~~ **CONFIRMED: No restart needed.** `bot.py` already uses `await bot.tree.sync(guild=discord_guild)` in `on_ready`. The sync button will reuse this exact pattern via `get_bot()`. The only extra step is removing stale subject commands from the tree before re-registering to prevent duplicates.
3. **`/quote` random behavior:** Should `/quote` have an optional `person:` parameter (autocomplete dropdown) so users can request a specific person's quote without knowing the exact command? Could replace per-person commands entirely. **Decision:** Keep both — per-person commands for personality, `/quote person:` as a fallback.
4. **Existing Mito data:** After migration, should we prompt admin to assign existing unowned quotes, or silently assign to a "legacy" pool? → Assign during migration if Mito player record exists, otherwise leave as `subject_id=NULL` (unassigned) and show a banner in admin UI.

---

## Acceptance Criteria

- [ ] `patt.quote_subjects` table exists with slug uniqueness enforced at DB level
- [ ] `guild_quotes.subject_id` and `guild_quote_titles.subject_id` are populated (no orphans after migration)
- [ ] Bot registers one slash command per active subject at startup
- [ ] `/mito` (or equivalent) returns a quote formatted with player name, their own title pool, and guild+realm footer
- [ ] `/quote` picks a random active subject and returns one of their quotes
- [ ] Admin `/admin/quotes` page lists all subjects, allows selecting one to view/edit their quotes
- [ ] Admin can add a new subject by searching for a player, assigning a slug and display name
- [ ] Admin can add/edit/delete quotes and titles for any subject (not just their own)
- [ ] Admin can deactivate a subject (hides from Discord and front page without deleting)
- [ ] "Sync Bot Commands" button triggers Discord command tree re-sync and confirms success
- [ ] Front page displays player attribution on the "Guild Quote of the Day" section
- [ ] Legacy `mitos-corner.html` no longer accepts write operations
- [ ] All new admin endpoints return 403 for non-officer users
- [ ] All existing tests continue to pass
- [ ] New unit + integration tests for subjects CRUD and command registration logic
