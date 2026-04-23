# Phase 1.8 — User Activity Logging

> **Goal:** Track basic web activity per user (page views, last seen, login count) and surface it on the Admin → Users page. Data persists in PostgreSQL so it survives deployments.

---

## What We're Tracking

Keep it simple and low-noise. We're not building an analytics platform — we want to answer "has this person actually used the site lately?" from the admin users table.

**Per user, we want to know:**
- Last time they visited any page (last active)
- Last time they logged in
- Total login count
- Which pages they visit (lightweight summary, not a full audit trail)
- First-party only — no external tracking, no JS beacon

**What we are NOT tracking:**
- API polling requests (the status-check endpoints that fire every few seconds)
- Static asset requests
- Bot/scanner traffic
- Request bodies or form data

---

## Architecture Decision

**Store in DB, write async, no request blocking.**

Options considered:
1. **In-memory counter + periodic flush** — data loss on restart, complex
2. **Write every request synchronously** — adds DB latency to every page load
3. **Write every request via background task** — chosen approach: FastAPI's `BackgroundTasks` fires after response is sent, so zero user-facing latency, and data lands in PostgreSQL (survives deployments, restarts, redeployments)

The write is a simple upsert on `common.user_activity` keyed on `(user_id, date)` — one row per user per day. A separate `common.users` column (`last_active_at`) gets updated on every meaningful request.

---

## Schema (Migration 0178)

### New columns on `common.users`

```sql
ALTER TABLE common.users
    ADD COLUMN last_active_at   TIMESTAMPTZ,
    ADD COLUMN last_login_at    TIMESTAMPTZ,
    ADD COLUMN login_count      INTEGER NOT NULL DEFAULT 0;
```

`last_active_at` — updated on every non-API-poll page load (background task).  
`last_login_at` — set on successful `POST /login`.  
`login_count` — incremented on each successful login.

### New table `common.user_activity`

One row per user per UTC date — a daily rollup. Keeps the write path cheap (upsert increment) and the history queryable without a huge row count.

```sql
CREATE TABLE common.user_activity (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES common.users(id) ON DELETE CASCADE,
    activity_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    page_views      INTEGER NOT NULL DEFAULT 0,
    pages_visited   TEXT[] NOT NULL DEFAULT '{}',   -- deduped list of paths visited that day
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, activity_date)
);

CREATE INDEX ix_user_activity_user_id     ON common.user_activity (user_id);
CREATE INDEX ix_user_activity_date        ON common.user_activity (activity_date DESC);
```

`pages_visited` stores the distinct page paths visited that day as a deduplicated TEXT array (e.g. `{"/my-characters", "/roster", "/admin/raid-tools"}`). Array append with dedup keeps it simple — no JSON needed.

**Retention:** rows older than 90 days are pruned by a weekly scheduler job. At ~30 active users that's ~2,700 rows max — trivial.

---

## Implementation Phases

### Phase A — Schema + Login tracking (migration 0178)

**Files touched:**
- `alembic/versions/0178_user_activity_logging.py` — migration
- `src/sv_common/auth/` — update login route to stamp `last_login_at` + increment `login_count`

**Login stamping** happens in `POST /api/v1/auth/login` (in `src/guild_portal/api/auth_routes.py`). After verifying password and before returning the JWT:

```python
await conn.execute("""
    UPDATE common.users
    SET last_login_at = NOW(),
        login_count = login_count + 1,
        updated_at = NOW()
    WHERE id = $1
""", user_id)
```

No background task needed — this is already in the login critical path.

---

### Phase B — Page view middleware ✅ COMPLETE (commit c43a34a, branch feature/user-activity-logging)

**Files touched:**
- `src/guild_portal/middleware/activity.py` — new file, ~60 lines
- `src/guild_portal/app.py` — register middleware

A Starlette middleware intercepts every response **after** it's been sent to the client. It:
1. Skips if no `patt_token` cookie (unauthenticated request)
2. Skips for paths matching an ignore list (static assets, API polling, health)
3. Decodes the JWT to get `user_id` (already validated by the route — we just need the ID, no DB round-trip)
4. Fires a `BackgroundTask` to write the upsert

**Paths to ignore** (defined as a prefix/regex set in the middleware):
```
/static/
/favicon.ico
/health
/api/v1/admin/bis/enrich-classify-status
/api/v1/admin/bis/landing-status
/api/v1/admin/bis/scrape-log
/api/v1/me/gear-plan/*/available-items   ← repeated slot polling
```

Member-facing API routes (e.g. `GET /api/v1/me/gear-plan/{id}`) and admin API reads (e.g. `GET /api/v1/admin/bis/matrix`) are counted — these represent genuine user interactions, not just page loads.

**Background upsert** (runs after response is flushed):

```python
async def _record_activity(pool, user_id: int, path: str):
    async with pool.acquire() as conn:
        today = date.today()
        await conn.execute("""
            INSERT INTO common.user_activity (user_id, activity_date, page_views, pages_visited)
            VALUES ($1, $2, 1, ARRAY[$3]::text[])
            ON CONFLICT (user_id, activity_date) DO UPDATE
            SET page_views    = user_activity.page_views + 1,
                pages_visited = CASE
                    WHEN $3 = ANY(user_activity.pages_visited)
                    THEN user_activity.pages_visited
                    ELSE user_activity.pages_visited || ARRAY[$3]::text[]
                END,
                updated_at = NOW()
        """, user_id, today, path)

        await conn.execute("""
            UPDATE common.users
            SET last_active_at = NOW(), updated_at = NOW()
            WHERE id = $1 AND (last_active_at IS NULL OR last_active_at < NOW() - INTERVAL '5 minutes')
        """, user_id)
```

The `last_active_at` update includes a 5-minute gate — no point hammering the users row on every click.

**Middleware skeleton:**

```python
class ActivityMiddleware(BaseHTTPMiddleware):
    IGNORE_PREFIXES = ("/static/", "/favicon", "/health")
    IGNORE_SUFFIXES = ("-status", "-log")   # polling endpoints

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        token = request.cookies.get("patt_token")
        if token and not self._should_skip(request.url.path):
            try:
                payload = decode_access_token(token)
                user_id = payload.get("user_id")
                if user_id:
                    background = BackgroundTask(
                        _record_activity, request.app.state.pool, user_id, request.url.path
                    )
                    response.background = background
            except Exception:
                pass   # expired/invalid token — silently skip
        return response
```

If the response already has a background task (rare), we chain them.

---

### Phase C — Admin Users page UI ✅ COMPLETE (commit 0baafeb, branch feature/user-activity-logging)

**Files touched:**
- `src/guild_portal/pages/admin_pages.py` — extend the `/admin/users` query
- `src/guild_portal/templates/admin/users.html` — new columns + activity panel

**Extended query** — add activity data to the existing users JOIN:

```sql
SELECT
    u.id,
    u.email,
    u.is_active,
    u.created_at,
    u.last_login_at,
    u.last_active_at,
    u.login_count,
    p.id           AS player_id,
    p.display_name,
    ...existing columns...,
    -- last 7 days page view total
    COALESCE(SUM(ua.page_views) FILTER (
        WHERE ua.activity_date >= CURRENT_DATE - 6
    ), 0) AS views_7d,
    -- last active date
    MAX(ua.activity_date) AS last_activity_date,
    -- all-time page views
    COALESCE(SUM(ua.page_views), 0) AS views_total
FROM common.users u
LEFT JOIN guild_identity.players p ON p.website_user_id = u.id
LEFT JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
LEFT JOIN guild_identity.battlenet_accounts ba ON ba.player_id = p.id
LEFT JOIN common.user_activity ua ON ua.user_id = u.id
GROUP BY u.id, p.id, gr.id, ba.id
ORDER BY u.last_active_at DESC NULLS LAST
```

Sorting by `last_active_at DESC` by default — most recently active users at the top, which is far more useful than creation order.

**New columns in the table:**

| Column | Value |
|--------|-------|
| Last Active | `last_active_at` relative ("2h ago", "3d ago", "never") |
| Last Login | `last_login_at` date |
| Logins | `login_count` |
| 7d Views | `views_7d` page views in the rolling 7 days |

**Activity detail row** — clicking a row (or a ▶ expand button) expands an inline sub-row showing the `pages_visited` array for the last 7 days as a compact tag list. No modal needed — the inline expand pattern is already used elsewhere in the admin templates.

**Stats pills** — the existing stat pills row gets two additions:
- "Active this week" — count of users with `last_active_at >= 7 days ago`
- "Never logged in" — count of users with `login_count = 0`

---

### Phase D — Retention pruning job

**Files touched:**
- `src/sv_common/guild_sync/scheduler.py` — add weekly prune job

```python
@scheduler.scheduled_job(CronTrigger(day_of_week="sun", hour=3))
async def prune_old_activity():
    async with pool.acquire() as conn:
        deleted = await conn.execute("""
            DELETE FROM common.user_activity
            WHERE activity_date < CURRENT_DATE - 90
        """)
        logger.info(f"Pruned old user_activity rows: {deleted}")
```

Runs Sunday 3am UTC. At ~30 users, 90-day retention = ~2,700 rows max — basically nothing.

---

## File Summary

| File | Change |
|------|--------|
| `alembic/versions/0178_user_activity_logging.py` | New migration — columns + table |
| `src/guild_portal/middleware/activity.py` | New — ActivityMiddleware + _record_activity |
| `src/guild_portal/app.py` | Register ActivityMiddleware |
| `src/sv_common/auth/` (login route) | Stamp last_login_at + increment login_count on successful login |
| `src/guild_portal/pages/admin_pages.py` | Extend /admin/users query with activity data |
| `src/guild_portal/templates/admin/users.html` | New columns, expand row, updated stat pills |
| `src/sv_common/guild_sync/scheduler.py` | Weekly prune job |

---

## Migration Safety

- All new columns are nullable or have defaults — zero downtime, no backfill required
- `login_count DEFAULT 0` — existing users start at 0 (accurate for "logins after this deploy")
- `last_active_at` and `last_login_at` start NULL — displayed as "never" in the UI, which is correct
- The `user_activity` table starts empty — that's fine, data accumulates naturally
- Rolling back: drop the table + columns, remove middleware registration — no data dependency in other tables

---

## Test Coverage

- Unit tests for `_record_activity()`: first visit creates row, subsequent visits increment, new day creates new row, path dedup works
- Unit tests for middleware path filtering: static/polling paths skipped, page paths tracked, unauthenticated requests skipped, expired token silently skipped
- Unit tests for login stamping: `last_login_at` and `login_count` updated on successful login
- Unit test for prune job: rows older than 90 days deleted, recent rows untouched
- Admin users query: returns `views_7d`, `views_total`, `last_active_at` correctly

Estimated: ~20 new tests across 3 test files.

---

## Build Order

**Phase A** → **Phase B** → **Phase C** → **Phase D**

Each phase is independently deployable. Phase C (UI) is the only user-visible change — Phases A and B can ship silently first and accumulate data before the UI is wired up.
