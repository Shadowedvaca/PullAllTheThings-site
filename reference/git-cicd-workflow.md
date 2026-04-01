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

## Server Architecture

Three environments, **three separate servers**. This is not optional — blast radius isolation is the point.

| Environment | SSH Alias | Region | Server | Purpose |
|-------------|-----------|--------|--------|---------|
| **dev** | `my-web-apps-dev` | Falkenstein, DE | CX23 (2vCPU / 4GB) | Sandbox. Break things here. Disposable. |
| **test** | `my-web-apps-test` | Falkenstein, DE | CX23 (2vCPU / 4GB) | Integration gate. Mirrors prod config. |
| **prod** | project-specific (e.g. `sv-tools`, `hetzner`) | Hillsboro, OR or Falkenstein | CPX21 / CX23 | Live. Real users/data. |

**Why separate servers:**
- Dev changes (schema experiments, model reloads, failed deploys) cannot cascade to prod or test
- Test must mirror prod config exactly — shared servers allow drift
- Prod-web (OR) is latency-sensitive; dev/test (Falkenstein) latency doesn't matter

See `reference/server-architecture.md` for full server inventory and per-app resource profiles.

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
| **dev** | Fast feedback sandbox. Break things here. | Manual trigger from feature branch → `my-web-apps-dev` |
| **test** | Integration gate. Matches prod config. | Auto on push to `main` (i.e. merged PR) → `my-web-apps-test` |
| **prod** | Live. Real users/data. | Auto on `prod-*` tag only → prod server |

---

## GitHub Actions Workflows

Each project must have **three workflow files** targeting the three servers:

### deploy-dev.yml — Manual, targets `my-web-apps-dev`

```yaml
name: Deploy Dev

on:
  workflow_dispatch:
    inputs:
      branch:
        description: 'Branch to deploy'
        required: true
        default: 'main'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to dev
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.DEV_HOST }}
          username: root
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /opt/<app-name>
            git fetch origin
            git reset --hard origin/${{ github.event.inputs.branch }}
            docker compose up -d --build
            docker image prune -f

      - name: Health check
        run: |
          sleep 10
          curl --fail https://dev.<app-domain>/api/health
```

### deploy-test.yml — Auto on push to `main`, targets `my-web-apps-test`

```yaml
name: Deploy Test

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to test
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.TEST_HOST }}
          username: root
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /opt/<app-name>
            git fetch origin
            git reset --hard origin/main
            docker compose up -d --build
            docker image prune -f

      - name: Health check
        run: |
          sleep 10
          curl --fail https://test.<app-domain>/api/health
```

### deploy.yml (prod) — Auto on `prod-*` tag, targets prod server

```yaml
name: Deploy Prod

on:
  push:
    tags:
      - 'prod-*'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to prod
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.PROD_HOST }}
          username: root
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /opt/<app-name>
            git fetch --tags
            git reset --hard ${{ github.ref_name }}
            docker compose up -d --build
            docker image prune -f

      - name: Health check
        run: |
          sleep 10
          curl --fail https://<app-domain>/api/health
```

---

## Required GitHub Secrets

Each project repo needs these secrets set under **Settings → Secrets → Actions**:

| Secret | Value |
|--------|-------|
| `DEV_HOST` | IP of `my-web-apps-dev` server |
| `TEST_HOST` | IP of `my-web-apps-test` server |
| `PROD_HOST` | IP of the prod server for this app |
| `DEPLOY_SSH_KEY` | Private key that has root access on all three servers |

> **Single key for all three servers** — add the same public key to `/root/.ssh/authorized_keys` on dev, test, and prod. The private key lives only in GitHub Secrets.

---

## Normal Feature Flow

```
1. Branch from main
   git checkout main && git pull
   git checkout -b feature/my-thing

2. Develop, iterate
   [write code, run tests locally]

3. Deploy to dev — verify it works
   git push origin feature/my-thing
   gh workflow run deploy-dev.yml -f branch=feature/my-thing
   # Deploys to my-web-apps-dev → https://dev.<app-domain>
   [verify in dev environment]

4. Merge to main → test auto-deploys
   git checkout main
   git merge feature/my-thing --no-ff
   git push origin main
   # deploy-test.yml fires → my-web-apps-test → https://test.<app-domain>
   [verify in test environment]

5. Tag to release to prod
   git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z
   # deploy.yml fires → prod server → https://<app-domain>
```

**Rules:**
- Always use `--no-ff` on merges so the branch history is visible in the log
- Delete feature branches after merge — don't let them accumulate
- Don't skip dev verification just because the change feels small
- Never deploy directly to prod by SSH — always go through the tag gate

---

## Hotfix Flow (something is broken in prod RIGHT NOW)

Hotfixes follow the same branch discipline — no shortcuts on that — but they have a fast lane to prod that bypasses the normal test-first requirement.

```
1. Branch from main (not from a stale feature branch)
   git checkout main && git pull
   git checkout -b hotfix/describe-the-break

2. Make the minimal fix
   [fix only what is broken]

3. Deploy to dev — confirm the fix works
   git push origin hotfix/describe-the-break
   gh workflow run deploy-dev.yml -f branch=hotfix/describe-the-break
   [verify fix in dev]

4. Merge directly to main and tag — test will auto-deploy but don't wait for it
   git checkout main
   git merge hotfix/describe-the-break --no-ff
   git push origin main
   git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z
   [prod deploys immediately via tag]

5. Verify prod is healthy, then clean up
   [smoke test prod]
   git branch -d hotfix/describe-the-break
```

**What makes hotfix different:**
- You still verify in dev (don't skip — a broken hotfix makes things worse)
- You **do not wait** for test to fully pass before tagging to prod
- Test will still deploy (main push triggers it) — treat it as a parallel smoke test
- Document the incident in the commit message

---

## Versioning

```
X.Y.Z
│ │ └── PATCH: bug fix, hotfix
│ └──── MINOR: new feature (feature/* branch)
└────── MAJOR: breaking change or major milestone
```

Tag format: `prod-vX.Y.Z` — always use this exact format. Workflows match on `prod-*`.

---

## Quick Reference

```bash
# --- NORMAL FEATURE ---
git checkout main && git pull
git checkout -b feature/thing
# ... work ...
git push origin feature/thing
gh workflow run deploy-dev.yml -f branch=feature/thing   # → my-web-apps-dev
git checkout main && git merge feature/thing --no-ff && git push origin main  # → my-web-apps-test (auto)
git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z       # → prod (auto)

# --- HOTFIX ---
git checkout main && git pull
git checkout -b hotfix/what-is-broken
# ... minimal fix ...
git push origin hotfix/what-is-broken
gh workflow run deploy-dev.yml -f branch=hotfix/what-is-broken  # → my-web-apps-dev
git checkout main && git merge hotfix/what-is-broken --no-ff && git push origin main
git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z  # → prod immediately
```

---

## Adapting to a New Project

When setting up CI/CD for a new project:

1. Create three workflow files matching the templates above
2. Replace `<app-name>` with the app directory name on the server (e.g. `/opt/sv-tools`)
3. Replace `<app-domain>` with the domain/subdomain (e.g. `sv-tools.shadowedvaca.com`)
4. Set `DEV_HOST`, `TEST_HOST`, `PROD_HOST`, and `DEPLOY_SSH_KEY` in GitHub repo secrets
5. Ensure the app directory exists on all three servers with the repo checked out
6. Confirm the deploy key's public key is in `/root/.ssh/authorized_keys` on all three servers

The three-workflow pattern is consistent across all projects. The only things that change per project are: host secrets, app directory path, and health check URL.

---

*Last updated: 2026-04-01*
