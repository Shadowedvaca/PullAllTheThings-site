# Git & CI/CD Workflow — Personal Standard

This document defines the canonical git and deployment workflow for all projects.
Drop a reference to this file in each project's CLAUDE.md so the rules travel with you.

---

## Philosophy

- **Branches are cheap. Direct commits to main are not.**
- Every environment has a gate. Dev is the sandbox. Test is the integration check. Prod is the contract.
- Hotfixes are legitimate — they need their own fast lane, not a different philosophy.
- main should always reflect what is in or about to go to test/prod. Keep it clean.

---

## Branch Types

| Prefix | Purpose | Version bump |
|--------|---------|-------------|
| `feature/*` | New functionality | MINOR (`x.Y.0`) |
| `fix/*` | Planned bug fix | PATCH (`x.y.Z`) |
| `hotfix/*` | Emergency production fix | PATCH (`x.y.Z`) |
| `chore/*` | Deps, docs, config, cleanup | none |
| `refactor/*` | Internal restructuring, no behavior change | none |

---

## Environments

Three environments, three gates:

| Environment | Purpose | Deployed by |
|-------------|---------|-------------|
| **dev** | Fast feedback sandbox. Break things here. | Manual trigger from feature branch |
| **test** | Integration gate. Matches prod config. | Auto on push to `main` (i.e. merged PR) |
| **prod** | Live. Real users/data. | Auto on `prod-*` tag only |

---

## Normal Feature Flow

```
1. Branch from main
   git checkout main && git pull
   git checkout -b feature/my-thing

2. Develop, iterate
   [write code, run tests]

3. Deploy to dev — verify it works
   git push origin feature/my-thing
   gh workflow run deploy-dev.yml -f branch=feature/my-thing
   [verify in dev environment]

4. Merge to main → test auto-deploys
   git checkout main
   git merge feature/my-thing --no-ff
   git push origin main
   [verify in test environment]

5. Tag to release to prod
   git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z
```

**Rules:**
- Always use `--no-ff` on merges so the branch history is visible in the log
- Delete feature branches after merge — don't let them accumulate
- Don't skip dev verification just because the change feels small

---

## Hotfix Flow (something is broken in prod RIGHT NOW)

Hotfixes follow the same branch discipline — no shortcuts on that — but they have a fast lane to prod that bypasses the normal test-first requirement.

```
1. Branch from main (not from a stale feature branch)
   git checkout main && git pull
   git checkout -b hotfix/describe-the-break

2. Make the MINIMAL fix — only what is broken, nothing else

3. Verify in dev
   git push origin hotfix/describe-the-break
   gh workflow run deploy-dev.yml -f branch=hotfix/describe-the-break
   [confirm the fix works]

4. Merge to main + tag prod immediately (test deploys as a side effect, that's fine)
   git checkout main
   git merge hotfix/describe-the-break --no-ff
   git push origin main                          ← this auto-deploys test
   git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z  ← this deploys prod
```

**Hotfix rules:**
- Hotfixes always branch from `main` — never from a feature branch
- Fix only the broken thing. No opportunistic cleanup. No "while I'm in here..."
- Merge back to main immediately so test stays current with prod
- Document in the commit message that this is a hotfix and why it bypassed normal flow
- If the hotfix temporarily breaks test, that is acceptable — fix it on the next regular cycle

---

## Emergency Patch (something is broken mid-feature, blocking current work)

Same as hotfix, just scoped to unblock active work rather than a prod incident. Same rules apply: branch, minimal fix, dev verify, merge to main, tag if needed.

The distinction is only semantic — the process is identical.

---

## Version Numbering — `MAJOR.MINOR.PATCH`

| Segment | When to bump | Reset on bump |
|---------|-------------|---------------|
| **MAJOR** | Breaking changes, full architecture overhaul, major new system | MINOR and PATCH → 0 |
| **MINOR** | New feature, new endpoint, new module, meaningful new capability | PATCH → 0 |
| **PATCH** | Bug fix, hotfix, data/content change, config tweak, docs | — |

**In practice:** most day-to-day work bumps PATCH. A shipping milestone bumps MINOR. MAJOR is rare.

Tag format: `prod-vMAJOR.MINOR.PATCH` (e.g. `prod-v0.1.6`)

---

## Key Rules — Never Break These

1. **Never commit directly to `main`** — always a branch + merge, even for a 1-line fix
2. **Never skip the branch step** — hotfixes still get branches, just shorter ones
3. **main is always deployable** — if main is broken, that is a P0
4. **Tags are permanent** — never reuse or force-push a tag
5. **Hotfix ≠ license for scope creep** — fix the one thing, ship it, move on
6. **Dev verify before prod tag** — even on hotfixes, confirm the fix in dev first

---

## Quick Reference

```bash
# --- NORMAL FEATURE ---
git checkout main && git pull
git checkout -b feature/thing
# ... work ...
git push origin feature/thing
gh workflow run deploy-dev.yml -f branch=feature/thing   # verify in dev
git checkout main && git merge feature/thing --no-ff && git push origin main  # → test
git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z       # → prod

# --- HOTFIX ---
git checkout main && git pull
git checkout -b hotfix/what-is-broken
# ... minimal fix ...
git push origin hotfix/what-is-broken
gh workflow run deploy-dev.yml -f branch=hotfix/what-is-broken  # verify
git checkout main && git merge hotfix/what-is-broken --no-ff && git push origin main
git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z
```

---

## Adapting to a New Project

When setting up CI/CD for a new project, the three-workflow pattern should mirror this structure:

| File | Trigger | Target |
|------|---------|--------|
| `deploy-dev.yml` | `workflow_dispatch` with `branch` input | dev environment |
| `deploy-test.yml` | `push: branches: [main]` | test environment |
| `deploy.yml` | `push: tags: ['prod-*']` | prod environment |

Each workflow should: checkout the branch/tag → build → copy to server → restart container → health check.

---

*Last updated: 2026-03-17*
