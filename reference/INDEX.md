# PATT Guild Platform — Context Files Index

> These files are designed for use with Claude Code. Each phase can be executed
> independently — clear your conversation, point Claude Code at the repo, and
> start the next phase.

---

## How to Use These Files

### Starting a New Phase

1. Clear your Claude Code conversation (saves context/costs)
2. Tell Claude Code: "Read CLAUDE.md, TESTING.md, and the phase file — then execute the phase"
3. Claude Code reads the files, understands the full project context, and begins work
4. At the end of the phase, Claude Code updates CLAUDE.md's "Current Build Status"
5. Commit, review, repeat

**Repo:** `Shadowedvaca/PullAllTheThings-site` — all context files live in this repo
alongside the existing legacy files. The platform is built in place, not in a new repo.

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

### Current Phase
| File | Phase | Summary |
|------|-------|---------|
| `reference/PHASE_2_8_SCHEDULING_AND_ATTENDANCE.md` | 2.8 | Weighted scheduling, time-window availability, attendance/season tables, admin reference table editor |

### Reference Documents (still useful context)
| File | Purpose |
|------|---------|
| `reference/PHASE_2_5_OVERVIEW.md` | Guild identity system overview — officer list, rank mappings, guild details |
| `reference/PHASE_2_6_ONBOARDING.md` | Onboarding system design — built but not yet activated |
| `data/contest_agent_personality.md` | Bot personality and message templates for campaigns |
| `docs/OPERATIONS.md` | How to operate the platform day-to-day |
| `docs/DISCORD-BOT-SETUP.md` | Discord bot configuration guide |
| `docs/RAID-HELPER-API-KEY.md` | Raid-Helper API key setup and usage |
| `docs/shadowedvaca-conversion-plan.md` | Future plan for shadowedvaca.com to use common services |
| `memory/MEMORY.md` | Running session state log |

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
| **2.8** | **🔜 Current** | **Scheduling, availability, attendance foundation** |

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
| ADMIN-SETUP-GUIDE.md | Legacy Google Sheets admin system (fully replaced) |
