# Phase 6.3 — Discord Notifications & Weekly Digest

> **Branch:** `phase-6-error-handling` (continue from Phase 6.2)
> **Migration:** none
> **Depends on:** Phase 6.1 (`sv_common.errors`) + Phase 6.2 (`error_routing` cache + table)
> **Produces:** Immediate Discord alerts on first occurrence; Sunday morning unresolved digest

---

## Goal

Wire `report_error()` up to Discord. Two behaviors:

1. **Immediate notification** — when `report_error()` returns `is_first_occurrence=True`
   (or `first_only=False`), and the routing rule says `dest_discord=True`, post to the
   audit channel right away.

2. **Weekly digest** — Sunday 8:00 AM UTC, post a grouped summary of all still-open errors
   to the audit channel. Keeps noise down — long-running unresolved issues don't spam
   Discord daily, but they don't disappear from officer awareness either.

No new tables. No migration. Pure logic in `guild_portal`.

---

## Prerequisites

- Phase 6.1 complete: `sv_common.errors.report_error()`, `resolve_issue()`, `get_unresolved()`
- Phase 6.2 complete: `common.error_routing` seeded, `error_routing.py` cache helper,
  `/admin/error-routing` page live
- Familiar with `src/sv_common/guild_sync/scheduler.py` — this is where the new jobs live
- Familiar with `src/sv_common/guild_sync/reporter.py` — existing `send_error()` and embed
  patterns to follow
- Discord bot running; `self._get_audit_channel()` on the scheduler returns the channel

---

## Key Files to Read Before Starting

- `src/sv_common/guild_sync/scheduler.py` — add `run_weekly_error_digest()`; understand
  `self.db_pool`, `self.discord_bot`, `self._get_audit_channel()`, `self.audit_channel_id`
- `src/sv_common/guild_sync/reporter.py` — `send_error()`, `send_sync_summary()` patterns
  for embed construction; `SEVERITY_COLORS` dict
- `src/guild_portal/services/error_routing.py` — `get_routing_rule(pool, type, severity)`
  from Phase 6.2
- `src/sv_common/errors/__init__.py` — `report_error()` return shape, `get_unresolved()`

---

## Part 1: Immediate Discord Notification

### Where This Lives

The immediate Discord notification happens at any callsite that has both a pool AND the
Discord bot. In practice, that means the **scheduler** — it is the only place in the
codebase where both are available simultaneously.

For callsites that don't have the bot (e.g., `admin_pages.py` HTTP handlers), `report_error()`
is still called — the error is catalogued — but no Discord post happens inline. The weekly
digest will surface it. This is intentional and correct.

### Helper Function

Add to `src/guild_portal/services/error_routing.py` (extends the Phase 6.2 file):

```python
async def maybe_notify_discord(
    pool: asyncpg.Pool,
    bot: discord.Client,
    audit_channel_id: int,
    issue_type: str,
    severity: str,
    summary: str,
    is_first_occurrence: bool,
) -> None:
    """
    Post to the audit Discord channel if the routing rule says to.

    Call this immediately after report_error() returns, passing its is_first_occurrence.
    Does nothing if:
    - routing rule says dest_discord=False
    - routing rule says first_only=True AND is_first_occurrence=False
    - bot is None or audit_channel_id is None
    """
    if bot is None or audit_channel_id is None:
        return

    rule = await get_routing_rule(pool, issue_type, severity)
    if not rule["dest_discord"]:
        return
    if rule["first_only"] and not is_first_occurrence:
        return

    from sv_common.guild_sync.reporter import send_error
    channel = bot.get_channel(audit_channel_id)
    if channel is None:
        return

    await send_error(channel, _format_title(issue_type, severity), summary)


def _format_title(issue_type: str, severity: str) -> str:
    """Convert issue_type to a readable title for the Discord embed."""
    from sv_common.guild_sync.reporter import ISSUE_TYPE_NAMES
    label = ISSUE_TYPE_NAMES.get(issue_type, issue_type.replace("_", " ").title())
    prefix = {"critical": "CRITICAL", "warning": "Warning", "info": "Notice"}.get(severity, severity.title())
    return f"{prefix}: {label}"
```

### Usage Pattern at Callsites (Preview of Phase 6.4)

When Phase 6.4 migrates the scheduler callsites, every error path will follow this pattern:

```python
from sv_common.errors import report_error, resolve_issue
from guild_portal.services.error_routing import maybe_notify_discord

# On failure:
result = await report_error(
    self.db_pool,
    "bnet_token_expired",
    "warning",
    f"Battle.net token expired for {battletag} — player must re-link",
    "scheduler",
    identifier=battletag,
)
await maybe_notify_discord(
    self.db_pool, self.discord_bot, self.audit_channel_id,
    "bnet_token_expired", "warning",
    result["summary"],        # or pass summary directly
    result["is_first_occurrence"],
)

# On success:
await resolve_issue(self.db_pool, "bnet_token_expired", identifier=battletag)
```

Phase 6.3 does NOT implement the callsite migration (that's Phase 6.4). It only provides
`maybe_notify_discord()` so Phase 6.4 has something to call.

---

## Part 2: Weekly Error Digest

### New Scheduler Job: `run_weekly_error_digest`

Add to `GuildSyncScheduler` in `src/sv_common/guild_sync/scheduler.py`.

**Schedule:** Sunday at 8:00 AM UTC.

```python
self.scheduler.add_job(
    self.run_weekly_error_digest,
    CronTrigger(day_of_week="sun", hour=8, minute=0),
    id="weekly_error_digest",
    replace_existing=True,
    misfire_grace_time=3600,
)
```

**Implementation:**

```python
async def run_weekly_error_digest(self):
    """
    Post a grouped summary of all open errors to the audit channel.
    Runs Sunday 8:00 AM UTC. Silent if no open errors.
    """
    from sv_common.errors import get_unresolved

    audit_channel = self._get_audit_channel()
    if audit_channel is None:
        logger.warning("Weekly error digest: audit channel not available")
        return

    try:
        errors = await get_unresolved(self.db_pool, limit=200)
    except Exception as exc:
        logger.error("Weekly error digest: failed to fetch errors: %s", exc)
        return

    if not errors:
        logger.info("Weekly error digest: no open errors")
        return

    embeds = _build_digest_embeds(errors)
    try:
        for i in range(0, len(embeds), 10):
            await audit_channel.send(embeds=embeds[i:i+10])
    except Exception as exc:
        logger.error("Weekly error digest: failed to post to Discord: %s", exc)
```

### Digest Embed Builder

Add as a module-level function in `scheduler.py` (or extract to `reporter.py` if preferred):

```python
def _build_digest_embeds(errors: list[dict]) -> list[discord.Embed]:
    """
    Build a list of Discord embeds for the weekly error digest.
    One header embed + one embed per issue_type group.
    """
    from sv_common.guild_sync.reporter import ISSUE_EMOJI, ISSUE_TYPE_NAMES, SEVERITY_COLORS
    from sv_common.config_cache import get_accent_color_int

    # Group by issue_type
    grouped: dict[str, list[dict]] = {}
    for err in errors:
        grouped.setdefault(err["issue_type"], []).append(err)

    # Determine overall worst severity
    sev_order = {"info": 0, "warning": 1, "critical": 2}
    worst = max(errors, key=lambda e: sev_order.get(e["severity"], 0))["severity"]

    header = discord.Embed(
        title="📋 Weekly Error Digest",
        description=(
            f"**{len(errors)} open issue{'s' if len(errors) != 1 else ''}** "
            f"across {len(grouped)} type{'s' if len(grouped) != 1 else ''}.\n"
            f"Manage at **Admin → Error Routing**."
        ),
        color=SEVERITY_COLORS.get(worst, get_accent_color_int()),
        timestamp=datetime.now(timezone.utc),
    )
    embeds = [header]

    for issue_type, group in grouped.items():
        emoji = ISSUE_EMOJI.get(issue_type, "🔴")
        label = ISSUE_TYPE_NAMES.get(issue_type, issue_type.replace("_", " ").title())
        worst_sev = max(group, key=lambda e: sev_order.get(e["severity"], 0))["severity"]
        color = SEVERITY_COLORS.get(worst_sev, 0x3498DB)

        lines = []
        for err in group[:15]:  # cap at 15 per type
            identifier = f" `{err['identifier']}`" if err["identifier"] else ""
            count = f" · {err['occurrence_count']}×" if err["occurrence_count"] > 1 else ""
            first = err["first_occurred_at"]
            age = f"first seen <t:{int(first.timestamp())}:R>"
            lines.append(f"• {identifier} — {err['summary'][:80]}{count} ({age})")

        if len(group) > 15:
            lines.append(f"*...and {len(group) - 15} more*")

        desc = "\n".join(lines)
        if len(desc) > 4000:
            desc = desc[:3990] + "\n*...truncated*"

        embeds.append(discord.Embed(
            title=f"{emoji} {label} ({len(group)})",
            description=desc,
            color=color,
        ))

    return embeds
```

### Digest Format (What Officers See)

```
📋 Weekly Error Digest
3 open issues across 2 types.
Manage at Admin → Error Routing.
─────────────────────────────────
🔑 Battle.net Token Expired (2)
• `sevin1979#1865` — token expired, player must re-link · 7× (first seen 5 days ago)
• `Shadowedvaca#1947` — token expired, player must re-link · 6× (first seen 4 days ago)
─────────────────────────────────
🔴 WCL Sync Failed (1)
• API rate limit on guild report fetch · 14× (first seen 13 days ago)
```

---

## Tests

### `tests/unit/test_discord_notifications.py`

**`test_maybe_notify_discord_posts_on_first_occurrence`**
Rule: `dest_discord=True, first_only=True`. `is_first_occurrence=True`.
`send_error` should be called once.

**`test_maybe_notify_discord_suppresses_repeat`**
Rule: `dest_discord=True, first_only=True`. `is_first_occurrence=False`.
`send_error` should NOT be called.

**`test_maybe_notify_discord_posts_repeat_when_first_only_false`**
Rule: `dest_discord=True, first_only=False`. `is_first_occurrence=False`.
`send_error` should be called.

**`test_maybe_notify_discord_suppresses_when_dest_discord_false`**
Rule: `dest_discord=False`. Any `is_first_occurrence`.
`send_error` should NOT be called.

**`test_maybe_notify_discord_noop_when_bot_none`**
`bot=None`. `send_error` should NOT be called, no exception raised.

**`test_maybe_notify_discord_noop_when_channel_id_none`**
`audit_channel_id=None`. Same — no-op.

### `tests/unit/test_digest_builder.py`

**`test_build_digest_embeds_empty`**
`_build_digest_embeds([])` — should not be called with empty list, but if called, returns
header only or empty list without crashing.

**`test_build_digest_embeds_single_type`**
Two errors, same type. Returns header embed + one type embed. Type embed title includes
count `(2)`.

**`test_build_digest_embeds_multiple_types`**
Three errors across two types. Returns header + two type embeds.

**`test_build_digest_embeds_caps_at_15_per_type`**
20 errors of the same type. Type embed description contains "...and 5 more".

**`test_build_digest_embeds_occurrence_count_shown`**
Error with `occurrence_count=7`. Line includes `7×`.

**`test_build_digest_embeds_identifier_shown`**
Error with `identifier="sevin1979#1865"`. Line includes the identifier.

### `tests/unit/test_scheduler.py` (additions)

**`test_scheduler_registers_weekly_digest_job`**
`GuildSyncScheduler.start` registers a job with `id="weekly_error_digest"`.

**`test_run_weekly_error_digest_silent_when_no_errors`**
`get_unresolved` returns `[]`. Audit channel `send` is NOT called.

**`test_run_weekly_error_digest_posts_when_errors_exist`**
`get_unresolved` returns one error. Audit channel `send` IS called.

**`test_run_weekly_error_digest_handles_missing_channel`**
`_get_audit_channel()` returns `None`. No exception raised.

---

## Deliverables Checklist

- [ ] `src/guild_portal/services/error_routing.py` — add `maybe_notify_discord()` and `_format_title()`
- [ ] `src/sv_common/guild_sync/scheduler.py` — add `run_weekly_error_digest()` job + registration
- [ ] `src/sv_common/guild_sync/scheduler.py` — add `_build_digest_embeds()` helper
- [ ] `tests/unit/test_discord_notifications.py` — all notification routing tests
- [ ] `tests/unit/test_digest_builder.py` — all digest builder tests
- [ ] `tests/unit/test_scheduler.py` — digest job registration + behavior tests
- [ ] `pytest tests/unit/ -v` — all existing tests still pass

---

## What This Phase Does NOT Do

- No changes to existing error callsites (Phase 6.4)
- `maybe_notify_discord()` exists but is not yet called from anywhere — Phase 6.4 wires it in
- No per-callsite migration — the scheduler `run_bnet_character_refresh` etc. still use old patterns
- No changes to `guild_identity.audit_issues` or the integrity checker
