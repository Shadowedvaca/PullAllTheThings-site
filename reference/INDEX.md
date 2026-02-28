# PATT Guild Platform — Context Files Index

> These files are designed for use with Claude Code. Each phase can be executed
> independently — clear your conversation, point Claude Code at the repo, and
> start the next phase.

---

## How to Use These Files

### Starting a New Phase

1. Clear your Claude Code conversation (saves context/costs)
2. Tell Claude Code: "Read CLAUDE.md and the phase file — then execute the phase"
3. Claude Code reads the files, understands the full project context, and begins work
4. At the end of the phase, Claude Code updates CLAUDE.md's "Current Build Status"
5. Commit, review, repeat

**Repo:** `Shadowedvaca/PullAllTheThings-site` — all context files live in this repo.

### The Persistent Memory

**CLAUDE.md** is the file that carries context across phases. It describes:
- What the project is and how it's built
- The full architecture, tech stack, and directory structure
- The database schema
- Code conventions and patterns
- What has been built so far (updated after each phase)

This means Claude Code never needs conversation history. It reads the repo.

---

## Active Files

### Core Context (read these every time)
| File | Purpose |
|------|---------|
| `CLAUDE.md` | Master project context — read this first, always |
| `TESTING.md` | Testing strategy, conventions, framework setup |

### Reference Documents (useful context)
| File | Purpose |
|------|---------|
| `reference/PHASE_2_5_OVERVIEW.md` | Guild identity system overview — officer list, rank mappings, guild details |
| `reference/PHASE_2_6_ONBOARDING.md` | Onboarding system design — built but not yet activated |
| `context/contest_agent_personality.md` | Bot personality and message templates for campaigns |
| `docs/OPERATIONS.md` | How to operate the platform day-to-day |
| `docs/DISCORD-BOT-SETUP.md` | Discord bot configuration guide |
| `docs/BACKUPS.md` | Automated backup and restore procedures |
| `RAID-HELPER-API-KEY.md` | How to get/manage the Raid-Helper API key |
| `DISCORD-DEVELOPER-MODE.md` | How to enable Developer Mode and copy Discord IDs |
| `memory/MEMORY.md` | Running cross-session notes |

---

## Phase History

| Phase | Status | Summary |
|-------|--------|---------|
| 0 | ✅ Complete | Server infrastructure, project scaffolding |
| 1 | ✅ Complete | Common services — identity & guild data model |
| 2 | ✅ Complete | Auth system, Discord bot setup |
| 3 | ✅ Complete | Campaign engine, ranked-choice voting |
| 4 | ✅ Complete | Web UI — pages, templates, vote interface |
| 5 | ✅ Complete | Legacy migration, static serving |
| 6 | ✅ Complete | Contest agent Discord integration |
| 7 | ✅ Complete | Polish, deployment, art vote launch |
| 2.5A–D | ✅ Complete | Guild identity system (Blizzard API, Discord sync, addon, integrity) |
| 2.6 | ⚙️ Built | Onboarding — code exists, not yet activated |
| 2.7 | ✅ Complete | Data model migration — 3NF rebuild, Player model, reference tables |
| 2.8 | ✅ Complete | Crafting Corner — profession/recipe DB, public page, adaptive sync |
| 2.9 | ✅ Complete | Data Quality Engine — 8-rule registry, mitigations, admin page |
| 3.0A | ✅ Complete | Matching transparency — link_source/confidence, coverage dashboard |
| 3.0B | ✅ Complete | Iterative rule runner — pluggable matching_rules, per-rule UI |
| 3.0C | ✅ Complete | Drift Detection — 3 drift rules, drift_scanner, drift panel |
| 3.0D | ✅ Complete | Player Manager QoL — deletion guard, /admin/users, alias chips |
| 3.1 | ✅ Complete | Admin Availability Dashboard — recurring_events, 7-day grid |
| 3.2 | ✅ Complete | Index Page Revamp — officers, recruiting, schedule from DB |
| 3.3 | ✅ Complete | Public Roster — Full Roster, Composition, Schedule tabs |
| 3.4 | ✅ Complete | Admin Raid Tools — Raid-Helper integration, event builder |
| 3.5 | ✅ Complete | Auto-Booking Scheduler — background loop, auto-creates weekly raid |
| 3.6 | ✅ Complete | Roster Initiate Filtering + Raid Hiatus — on_raid_hiatus flag, New Members box, Show Initiates checkbox |

---

## Archived Files

Completed phase plans have been moved to `reference/archive/`. These are historical
build instructions — the work they describe is done. Refer to them only if you need
to understand what was originally specified for a completed phase.

| File | Original Phase |
|------|----------------|
| PHASE-0.md through PHASE-7.md | Phases 0–7 |
| PHASE_2_5A.md through PHASE_2_5D.md | Phase 2.5 sub-phases |
| PHASE_2_7_DATA_MODEL_MIGRATION.md | Phase 2.7 |
| PHASE_2_8_CRAFTING_CORNER.md | Phase 2.8 |
| PHASE_2_9_DATA_QUALITY_ENGINE.md | Phase 2.9 |
| ADMIN-SETUP-GUIDE.md | Legacy Google Sheets admin system (fully replaced) |
