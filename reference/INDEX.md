# PATT Guild Platform — Context Files Index

> These files are designed for use with Claude Code. Each phase can be executed
> independently — clear your conversation, point Claude Code at the repo, and
> start the next phase.

---

## How to Use These Files

### Starting a New Phase

1. Clear your Claude Code conversation (saves context/costs)
2. Tell Claude Code: "Read CLAUDE.md, TESTING.md, and phases/PHASE-{N}.md — then execute Phase {N}"
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

## File Inventory

### Core Context Files
| File | Purpose |
|------|---------|
| `CLAUDE.md` | Master project context — read this first, always |
| `TESTING.md` | Testing strategy, conventions, framework setup |

### Phase Implementation Plans
| File | Phase | Summary |
|------|-------|---------|
| `phases/PHASE-0.md` | 0 | Server infrastructure, project scaffolding, testing framework |
| `phases/PHASE-1.md` | 1 | Common services — identity & guild data model |
| `phases/PHASE-2.md` | 2 | Authentication & Discord bot |
| `phases/PHASE-3.md` | 3 | Campaign engine & voting API |
| `phases/PHASE-4.md` | 4 | Frontend — vote UI, results, admin pages |
| `phases/PHASE-5.md` | 5 | Google Sheets migration |
| `phases/PHASE-6.md` | 6 | Contest agent — Discord campaign updates |
| `phases/PHASE-7.md` | 7 | Polish & the art vote goes live |

### Supporting Documents
| File | Purpose |
|------|---------|
| `data/contest_agent_personality.md` | Bot personality and message templates for campaigns |
| `docs/shadowedvaca-conversion-plan.md` | Plan for evolving shadowedvaca.com to use common services |

---

## Phase Dependencies

```
Phase 0: Infrastructure & Scaffolding
    ↓
Phase 1: Identity & Guild Data Model
    ↓
Phase 2: Auth & Discord Bot
    ↓
Phase 3: Campaign Engine & Voting
    ↓
Phase 4: Frontend (Vote UI, Admin)
    ↓
Phase 5: Google Sheets Migration
    ↓
Phase 6: Contest Agent
    ↓
Phase 7: Polish & Go Live
```

Each phase builds on the previous. Do not skip phases.
Each phase is designed to be completable in a single Claude Code session.
