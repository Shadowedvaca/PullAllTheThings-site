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
| `reference/PHASE_2_7_DATA_MODEL_MIGRATION.md` | 2.7 | Clean 3NF data model rebuild — new reference tables, players entity, bridge tables, FK repoints |

### Reference Documents (still useful context)
| File | Purpose |
|------|---------|
| `reference/PHASE_2_5_OVERVIEW.md` | Guild identity system overview — officer list, rank mappings, guild details |
| `reference/PHASE_2_6_ONBOARDING.md` | Onboarding system design — built but not yet activated, code will be updated by 2.7 |
| `data/contest_agent_personality.md` | Bot personality and message templates for campaigns |
| `docs/OPERATIONS.md` | How to operate the platform day-to-day |
| `docs/DISCORD-BOT-SETUP.md` | Discord bot configuration guide |
| `docs/shadowedvaca-conversion-plan.md` | Future plan for shadowedvaca.com to use common services |
| `memory/MEMORY.md` | Running session state log |

---

## Archived Files

Completed phase plans have been moved to `reference/archive/`. These are historical
build instructions — the work they describe is done. Refer to them only if you need
to understand what was originally specified for a completed phase.

| File | Phase | Summary |
|------|-------|---------|
| `PHASE-0.md` | 0 | Server infrastructure, project scaffolding, testing framework |
| `PHASE-1.md` | 1 | Common services — identity & guild data model |
| `PHASE-2.md` | 2 | Authentication & Discord bot |
| `PHASE-3.md` | 3 | Campaign engine & voting API |
| `PHASE-4.md` | 4 | Frontend — vote UI, results, admin pages |
| `PHASE-5.md` | 5 | Google Sheets migration |
| `PHASE-6.md` | 6 | Contest agent — Discord campaign updates |
| `PHASE-7.md` | 7 | Polish & the art vote goes live |
| `PHASE_2_5A_SCHEMA_AND_BLIZZARD.md` | 2.5A | PostgreSQL schema + Blizzard API client |
| `PHASE_2_5B_IDENTITY_ENGINE.md` | 2.5B | Matching engine + Discord sync + integrity checker |
| `PHASE_2_5C_ADDON_AND_COMPANION.md` | 2.5C | WoW Lua addon + Python companion app |
| `PHASE_2_5D_TESTS.md` | 2.5D | Test suite for identity system |

---

## Phase History

```
Phase 0: Infrastructure & Scaffolding ........... ✅ Complete
Phase 1: Identity & Guild Data Model ............ ✅ Complete (being replaced by 2.7)
Phase 2: Auth & Discord Bot ..................... ✅ Complete
Phase 3: Campaign Engine & Voting ............... ✅ Complete
Phase 4: Frontend (Vote UI, Admin) .............. ✅ Complete
Phase 5: Google Sheets Migration ................ ✅ Complete
Phase 6: Contest Agent .......................... ✅ Complete
Phase 7: Polish & Go Live ...................... ✅ Complete
Phase 2.5: Guild Identity & Integrity System .... ✅ Complete
Phase 2.6: Onboarding System ................... ⚙️  Built, not activated
Phase 2.7: Data Model Migration (3NF) .......... 🔜 CURRENT
```
