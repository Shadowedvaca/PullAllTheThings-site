# Phase 4.0 — Config Extraction & Genericization

## Goal

Remove every hardcoded guild-specific value from the codebase and replace with database-driven
configuration. Genericize the Mito Quotes feature into "Guild Quotes" and make the Contest Agent
brand-neutral. After this phase, the application is fully configurable for any guild without
touching code or templates.

---

## Prerequisites

- All existing tests pass (409+ pass, 69 skip)
- Current migration head: 0030

---

## Database Migration: 0031_site_config_and_genericize

### New Table: `common.site_config`

```sql
CREATE TABLE common.site_config (
    id              SERIAL PRIMARY KEY,
    guild_name      VARCHAR(100)  NOT NULL DEFAULT 'My Guild',
    guild_tagline   VARCHAR(255)  DEFAULT NULL,
    guild_mission   TEXT          DEFAULT NULL,
    discord_invite_url VARCHAR(255) DEFAULT NULL,
    accent_color_hex VARCHAR(7)   NOT NULL DEFAULT '#d4a84b',
    realm_display_name VARCHAR(50) DEFAULT NULL,        -- "Sen'jin" (display)
    home_realm_slug VARCHAR(50)   DEFAULT NULL,          -- "senjin" (API slug)
    guild_name_slug VARCHAR(100)  DEFAULT NULL,          -- "pull-all-the-things" (API slug)
    logo_url        VARCHAR(500)  DEFAULT NULL,
    enable_guild_quotes BOOLEAN   NOT NULL DEFAULT FALSE,
    enable_contests     BOOLEAN   NOT NULL DEFAULT TRUE,
    setup_complete      BOOLEAN   NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP     NOT NULL DEFAULT NOW()
);
```

### New Table: `common.rank_wow_mapping`

```sql
CREATE TABLE common.rank_wow_mapping (
    id              SERIAL PRIMARY KEY,
    wow_rank_index  INTEGER       NOT NULL,  -- 0-9 (Blizzard in-game rank index)
    guild_rank_id   INTEGER       NOT NULL REFERENCES common.guild_ranks(id),
    UNIQUE (wow_rank_index)
);
```

### Rename Mito Tables

```sql
ALTER TABLE patt.mito_quotes RENAME TO guild_quotes;
ALTER TABLE patt.mito_titles RENAME TO guild_quote_titles;
```

### Backfill for PATT Instance

The migration includes an INSERT that seeds `common.site_config` with PATT's current values
and `common.rank_wow_mapping` with the existing `RANK_NAME_MAP` entries, so the live instance
continues working identically after migration.

```sql
INSERT INTO common.site_config (
    guild_name, guild_tagline, guild_mission, discord_invite_url,
    accent_color_hex, realm_display_name, home_realm_slug, guild_name_slug,
    enable_guild_quotes, enable_contests, setup_complete
) VALUES (
    'Pull All The Things',
    'Casual Heroic Raiding with Real-Life Balance & Immaculate Vibes',
    'A WoW guild focused on casual heroic raiding with a real-life first philosophy and zero-toxicity culture.',
    'https://discord.gg/jgSSRBvjHM',
    '#d4a84b',
    'Sen''jin',
    'senjin',
    'pull-all-the-things',
    TRUE,
    TRUE,
    TRUE
);

-- Map existing RANK_NAME_MAP: {0: GL, 1: Officer, 2: Veteran, 3: Member, 4: Initiate}
INSERT INTO common.rank_wow_mapping (wow_rank_index, guild_rank_id)
SELECT idx, gr.id FROM (VALUES
    (0, 'Guild Leader'),
    (1, 'Officer'),
    (2, 'Veteran'),
    (3, 'Member'),
    (4, 'Initiate')
) AS v(idx, rank_name)
JOIN common.guild_ranks gr ON gr.name = v.rank_name;
```

---

## ORM Updates (`src/sv_common/db/models.py`)

### New Models

```python
class SiteConfig(Base):
    __tablename__ = "site_config"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_name: Mapped[str] = mapped_column(String(100), default="My Guild")
    guild_tagline: Mapped[Optional[str]] = mapped_column(String(255))
    guild_mission: Mapped[Optional[str]] = mapped_column(Text)
    discord_invite_url: Mapped[Optional[str]] = mapped_column(String(255))
    accent_color_hex: Mapped[str] = mapped_column(String(7), default="#d4a84b")
    realm_display_name: Mapped[Optional[str]] = mapped_column(String(50))
    home_realm_slug: Mapped[Optional[str]] = mapped_column(String(50))
    guild_name_slug: Mapped[Optional[str]] = mapped_column(String(100))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    enable_guild_quotes: Mapped[bool] = mapped_column(Boolean, default=False)
    enable_contests: Mapped[bool] = mapped_column(Boolean, default=True)
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())


class RankWowMapping(Base):
    __tablename__ = "rank_wow_mapping"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(primary_key=True)
    wow_rank_index: Mapped[int] = mapped_column(Integer, unique=True)
    guild_rank_id: Mapped[int] = mapped_column(ForeignKey("common.guild_ranks.id"))

    guild_rank: Mapped["GuildRank"] = relationship()
```

---

## Task 1: Jinja2 Context Processor

### File: `src/patt/app.py`

Add a middleware or startup-loaded cache that injects `site` into every template context.

```python
_site_config_cache: dict | None = None

async def load_site_config(pool) -> dict:
    """Load site_config row into a dict. Cached in-process, refreshed on admin save."""
    global _site_config_cache
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM common.site_config LIMIT 1")
    if row:
        _site_config_cache = dict(row)
        # Add computed fields
        _site_config_cache["accent_color_int"] = int(row["accent_color_hex"].lstrip("#"), 16)
    else:
        _site_config_cache = {"guild_name": "Guild Portal", "setup_complete": False}
    return _site_config_cache

def get_site_config() -> dict:
    """Return cached site config. Available after lifespan startup."""
    return _site_config_cache or {"guild_name": "Guild Portal", "setup_complete": False}
```

Update `templates.TemplateResponse` calls — or better, add `site` to Jinja2 globals in lifespan:

```python
templates.env.globals["site"] = get_site_config
```

Then in any template: `{{ site().guild_name }}`, `{{ site().accent_color_hex }}`.

### Cache Invalidation

When admin saves site_config changes, call `load_site_config(pool)` to refresh the cache.
No restart required.

---

## Task 2: Template Genericization

### Find/Replace Targets

Every occurrence of these hardcoded values must be replaced with template variables:

| Hardcoded Value | Replace With | Files |
|----------------|--------------|-------|
| `Pull All The Things` | `{{ site().guild_name }}` | base.html, base_admin.html, index.html, auth pages (~20 files) |
| `https://discord.gg/jgSSRBvjHM` | `{{ site().discord_invite_url }}` | base.html, index.html |
| `Casual Heroic Raiding...` | `{{ site().guild_tagline }}` | index.html |
| `Based on Sen'jin` | `Based on {{ site().realm_display_name }}` | index.html |
| Guild mission paragraph | `{{ site().guild_mission }}` | index.html |

### CSS Custom Property

In `src/patt/static/css/main.css`, the `--accent-gold` variable is already used consistently.
Add a `<style>` block in `base.html` that overrides it from config:

```html
<style>
  :root {
    --accent-gold: {{ site().accent_color_hex }};
  }
</style>
```

This way all CSS references to `--accent-gold` automatically pick up the guild's accent color
without touching the CSS file.

### Conditional Sections

```html
{% if site().enable_guild_quotes %}
  <!-- Guild Quotes of the Day section -->
{% endif %}
```

---

## Task 3: Discord Embed Color Extraction

### Files to Update

Every Python file that uses `0xD4A84B` must read from site_config instead:

| File | Current | New |
|------|---------|-----|
| `src/patt/services/contest_agent.py` | `PATT_GOLD = 0xD4A84B` | `get_site_config()["accent_color_int"]` |
| `src/sv_common/discord/bot.py` | `0xD4A84B` | `get_site_config()["accent_color_int"]` |
| `src/sv_common/guild_sync/reporter.py` | `0xD4A84B` | `get_site_config()["accent_color_int"]` |
| `src/sv_common/guild_sync/api/crafting_routes.py` | `0xD4A84B` | `get_site_config()["accent_color_int"]` |
| `src/sv_common/guild_sync/onboarding/conversation.py` | `0xD4A84B` | `get_site_config()["accent_color_int"]` |
| `src/sv_common/guild_sync/discord_sync.py` | `0xD4A84B` | `get_site_config()["accent_color_int"]` |

Add a helper function in `src/patt/app.py` (or a shared module) so these files don't all
import from `app.py`:

```python
# src/sv_common/config_cache.py (new file)
_cache = {}

def set_site_config(config: dict):
    _cache.update(config)

def get_accent_color_int() -> int:
    return _cache.get("accent_color_int", 0xD4A84B)

def get_guild_name() -> str:
    return _cache.get("guild_name", "Guild Portal")
```

Load this during app lifespan; all modules import from `sv_common.config_cache`.

---

## Task 4: Genericize Guild Quotes (formerly Mito)

### Rename Files

| Old | New |
|-----|-----|
| `src/patt/bot/mito_commands.py` | `src/patt/bot/guild_quote_commands.py` |

### Changes

- Slash command: `/mito` → `/quote`
- Command description: "Get a random guild quote" (generic)
- DB tables: `patt.mito_quotes` → `patt.guild_quotes`, `patt.mito_titles` → `patt.guild_quote_titles`
- Index page section: "Mito's Quote of the Day" → "Guild Quote of the Day" (only shown when
  `site_config.enable_guild_quotes = TRUE`)
- Feature gate: check `enable_guild_quotes` before registering the slash command in `on_ready`

### Admin UI

Add a simple admin page or section for managing quotes:
- `/admin/guild-quotes` — CRUD for quotes and titles
- Only visible in admin nav when `enable_guild_quotes` is TRUE

---

## Task 5: Genericize Contest Agent

### File: `src/patt/services/contest_agent.py`

- Remove any `PATT_GOLD` constant — use `get_accent_color_int()`
- Replace any "PATT" references in message templates with `get_guild_name()`
- Footer text: use `get_guild_name()` instead of hardcoded strings

### File: `data/contest_agent_personality.md`

- This is a reference file, not code. Leave as-is (it's PATT's personality).
  Other guilds can replace this file or ignore it.

---

## Task 6: Configurable Rank-to-WoW Mapping

### File: `src/sv_common/guild_sync/blizzard_client.py`

Remove the hardcoded `RANK_NAME_MAP` dict. Replace with a function that reads from
`common.rank_wow_mapping`:

```python
async def get_rank_name_map(pool) -> dict[int, str]:
    """Load WoW rank index → platform rank name mapping from DB."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT rwm.wow_rank_index, gr.name
            FROM common.rank_wow_mapping rwm
            JOIN common.guild_ranks gr ON gr.id = rwm.guild_rank_id
        """)
    return {row["wow_rank_index"]: row["name"] for row in rows}
```

### File: `src/sv_common/guild_sync/db_sync.py`

Update `sync_roster_data()` to accept the rank map as a parameter instead of importing
the constant.

### File: `src/sv_common/guild_sync/scheduler.py`

Load the rank map once at the start of `run_blizzard_sync()` and pass it through.

---

## Task 7: Onboarding Text Genericization

### File: `src/sv_common/guild_sync/onboarding/conversation.py`

Replace hardcoded strings:

| Current | New |
|---------|-----|
| `"Welcome to Pull All The Things! 🎮"` | `f"Welcome to {get_guild_name()}! 🎮"` |
| `"Hey there! Welcome to the PATT Discord!"` | `f"Hey there! Welcome to the {get_guild_name()} Discord!"` |
| `"Pull All The Things • Sen'jin"` | `f"{get_guild_name()}"` |

---

## Task 8: Companion App Default URL

### File: `companion_app/patt_sync_watcher.py`

Change the default API URL from `pullallthething.com` to empty string (require explicit config):

```python
API_URL = os.getenv("PATT_API_URL", "")
if not API_URL:
    logger.error("PATT_API_URL not set in environment")
    sys.exit(1)
```

---

## Task 9: Admin Site Config Page

### New Route: `GET /admin/site-config`

Page to edit `common.site_config` values:
- Guild name, tagline, mission
- Discord invite URL
- Accent color (color picker)
- Realm display name
- Feature toggles (guild quotes, contests)

### Endpoint: `PATCH /api/v1/admin/site-config`

Updates the single `site_config` row. After save, calls `load_site_config()` to refresh
the in-process cache.

### Nav Entry

Add to admin sidebar: "Site Config" (visible to Guild Leader rank only, level >= 5).

---

## Environment Variable Cleanup

After this phase, the following env vars are **no longer needed** (values moved to DB):

| Env Var | Replaced By |
|---------|-------------|
| `PATT_GUILD_REALM_SLUG` | `site_config.home_realm_slug` |
| `PATT_GUILD_NAME_SLUG` | `site_config.guild_name_slug` |

Keep them as **fallbacks** during transition: if `site_config` row doesn't exist (fresh
install before setup wizard), fall back to env vars. After setup wizard completes, DB values
take precedence.

---

## Tests

- Unit tests for `get_site_config()` / `config_cache` module
- Unit test for `get_rank_name_map()` with mock DB
- Template rendering test: verify `{{ site().guild_name }}` renders correctly
- Verify accent color CSS override renders in base.html
- Verify guild quotes feature gate (enabled/disabled)
- Verify contest agent uses dynamic color
- All existing tests must continue to pass

---

## Deliverables Checklist

- [ ] Migration 0031 (site_config, rank_wow_mapping, table renames)
- [ ] ORM models for SiteConfig and RankWowMapping
- [ ] `sv_common.config_cache` module
- [ ] Jinja2 context processor wired in app lifespan
- [ ] All templates genericized (no hardcoded guild name/invite/tagline)
- [ ] CSS accent color override in base.html
- [ ] Discord embed color reads from config in all 6+ files
- [ ] Guild Quotes feature (renamed from Mito, feature-gated)
- [ ] Contest agent genericized
- [ ] Rank mapping reads from DB, not hardcoded dict
- [ ] Onboarding text genericized
- [ ] Companion app default URL removed
- [ ] Admin Site Config page (GL-only)
- [ ] Tests pass
