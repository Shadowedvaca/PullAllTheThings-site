# Deployment & Operations Reference

> For day-to-day ops (restarting services, creating campaigns, etc.) see `docs/OPERATIONS.md`.
> This doc covers CI/CD, Docker environments, and server quirks relevant to development work.

---

## CI/CD — GitHub Actions

`reference/development-and-release.md` is authoritative for authorization,
promotion, versioning, release notes, evidence, and rollback. This section is
an operational inventory of implemented workflows:

| Workflow | Trigger | Target | Server |
|----------|---------|--------|--------|
| `deploy-dev.yml` | **Manual** (`gh workflow run deploy-dev.yml -f branch=X`) | `dev.pullallthethings.com` | `my-web-apps-dev` |
| `pull-request-validation.yml` | Pull request | Isolated GitHub runner/PostgreSQL | GitHub-hosted |
| `deploy-test.yml` | Approved push to **main** | `test.pullallthethings.com` | `my-web-apps-test` |
| `deploy-prod.yml` | Exact approved `prod-v*` attempt tag with enabled readiness and exact-SHA Test evidence | `pullallthethings.com` | `hetzner` |
| `publish-release.yml` | Successful production deployment | GitHub Release | GitHub-hosted |

- Each `development`, `test`, and `production` GitHub environment supplies its
  own `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, and `DEPLOY_SSH_KEY` secrets plus a
  `DEPLOY_USER` variable. Keys are unique per environment and authorized only on
  the matching host. See `docs/DEPLOYMENT-CONTROLS.md` for the exact non-secret
  GitHub and SSH enforcement contract.
- Deployment resolves and checks out an exact commit and builds the image. Before
  the migration-running container can start, it creates and inspects an atomic
  environment-specific custom-format database backup plus rollback manifest.
  It then starts the image, verifies runtime version/environment/commit and
  database health, and verifies the Alembic head.
- Never create or recreate a production tag outside the Promotion to production
  gate. A successfully deployed or released tag is permanently immutable. A
  failed, unpublished attempt tag may be retired only by the separate manual,
  evidence-preserving process in `reference/development-and-release.md`.
- Deployment uses native OpenSSH with strict supplied known-host verification;
  it never accepts a newly scanned host key during a deployment. Bounded client
  keepalives detect a broken transport instead of leaving the workflow waiting
  indefinitely.
- The remote deployment program is the checked-in
  `deploy/patt-remote-deploy.sh` file. Workflows must not stream that program on
  SSH standard input: Docker Buildx can consume the remaining stream and allow
  the remote shell to reach end-of-file before later gates execute. Preparation
  and activation use separate SSH sessions. Preparation can build and create a
  verified backup but cannot start or migrate the application; it emits periodic
  non-secret backup progress and a `PATT_DEPLOYMENT_PREPARED` sentinel. A new
  session revalidates the sealed preparation record before activation. The
  runner then requires `PATT_DEPLOYMENT_COMPLETE` after runtime identity,
  database health, migration head, and the atomic active-SHA marker are verified.
- Production readiness is enabled after the reviewed #54/#55 foundation and
  readiness change. This setting is not promotion authority.
- Production preflight must still find a successful
  `Deploy to Test` run for the exact tag-target SHA. Containment in `main` is not
  sufficient evidence.

## Delivery authority

Branch, PR, test, and production flow is defined only in
`reference/work-management.md` and `reference/development-and-release.md`.
This runbook does not authorize merge, tags, deployment, environment changes,
or rollback.

Database backup, restore-rehearsal, and rollback decision boundaries are in
`docs/BACKUPS.md`. A failed verified-backup step blocks deployment before the
new container can run migrations. Live restore remains separately authorized
destructive work.

### Failed unpublished attempt tag

This is an exceptional release-control operation, not rollback. First preserve
the issue, exact annotated object, dereferenced commit, and failed run. Then use
the authenticated Windows GitHub CLI context for this read-only check:

```text
python3 scripts/validate_failed_tag_retirement.py \
  --gh-command gh.exe \
  --repository Shadowedvaca/PullAllTheThings-site \
  --tag prod-vX.Y.Z \
  --expected-tag-object EXACT_ANNOTATED_OBJECT_SHA \
  --expected-commit EXACT_DEREFERENCED_COMMIT_SHA \
  --failed-attempt EXACT_FAILED_RUN_ID:EXACT_FAILED_RUN_COMMIT_SHA
```

Repeat `--failed-attempt RUN_ID:COMMIT_SHA` for every Production push run ever
associated with a reused attempt tag. The validator rejects undeclared runs,
declared runs missing from GitHub history, commit mismatches, and any deploy job
that did not terminate unsuccessfully.

The check scans all Production runs for that tag and all Releases, rejects any
successful deploy completion, incomplete evidence, prior tag/commit mismatch,
or Release, and rechecks externally mutable state. It never changes GitHub.
Only Mike's explicit authority permits deletion of the exact validated ref.
Correction must then complete PR and exact-SHA Test promotion; recreation still
waits for a new Promotion to production approval and remains manual.

---

## Docker Environments

Three environments on **three separate servers**. Dev and test are shared CX23 nodes.

### Prod — `hetzner`

```
/opt/guild-portal/
├── docker-compose.guild.yml   ← prod-only compose (app-prod + db-prod)
├── .env.prod
└── ...
```

- `guild-portal-app-prod-1` / `guild-portal-db-prod-1` — port 8100
- Nginx proxies `pullallthethings.com` → 8100

```bash
ssh hetzner

# View prod logs
docker logs guild-portal-app-prod-1 -f

# Run a migration on prod (only with explicit permission)
docker exec guild-portal-app-prod-1 alembic upgrade head

# Access prod DB (only with explicit permission)
# The legacy Production volume currently contains patt_user / patt_db.
# Do not infer a non-empty volume's identity from POSTGRES_* container values.
docker exec guild-portal-db-prod-1 psql -U patt_user patt_db

# Restart prod app
docker compose -f /opt/guild-portal/docker-compose.guild.yml restart app-prod
```

### Dev — `my-web-apps-dev`

```
/opt/guild-portal/
├── docker-compose.dev.yml   ← single-env compose (app + db)
├── .env                     ← dev env vars; DB_PASSWORD must match db service
└── ...
```

- Service names: `app`, `db` — port 8100
- Nginx proxies `dev.pullallthethings.com` → 8100 (behind htpasswd auth)

```bash
ssh my-web-apps-dev

# View dev logs
docker compose -f /opt/guild-portal/docker-compose.dev.yml logs app -f

# Run a migration on dev
docker compose -f /opt/guild-portal/docker-compose.dev.yml exec app alembic upgrade head

# Access dev DB
docker compose -f /opt/guild-portal/docker-compose.dev.yml exec db psql -U guild_user guild_db

# Restart dev app
docker compose -f /opt/guild-portal/docker-compose.dev.yml restart app
```

### Test — `my-web-apps-test` (91.99.121.21)

Same layout as dev, using `docker-compose.test.yml` and `test.pullallthethings.com`.

```bash
ssh my-web-apps-test
docker compose -f /opt/guild-portal/docker-compose.test.yml logs app -f
```

---

## Known Deploy Quirks

### Chrome "GitHub 404" After Restart

If Chrome shows a GitHub Pages 404 immediately after a deployment:
- Chrome is serving a stale cached socket from when this repo used GitHub Pages
- **Fix:** `chrome://net-internals/#sockets` → **Flush socket pools** → reload
- Not a server problem — occasional, happens when deploys coincide with Chrome socket reuse

### CRITICAL: `/etc/hosts` Override on the Hetzner Prod Server

> **Full migration checklist: `docs/SERVER-IP-MIGRATION.md`**

The prod server has a mandatory `/etc/hosts` entry forcing the domain to its own IP:

```
5.78.114.224    pullallthethings.com www.pullallthethings.com
```

**Why:** After DNS migration from GitHub Pages, Google DNS served stale GitHub A records for 24+ hours.
Self-directed `curl` calls (health checks, smoke tests) hit GitHub 404s instead of the app.

**Why it's in two places:** `cloud-init` with `manage_etc_hosts: True` regenerates `/etc/hosts` from a
template on every boot. Entry lives in both `/etc/hosts` (active) and `/etc/cloud/templates/hosts.debian.tmpl` (survives reboots).

**If you change the server IP or migrate to a new server:** Update this entry before running any smoke tests.
See `docs/SERVER-IP-MIGRATION.md` for the full checklist.

---

## Local Development

```bash
# Create venv (first time)
python -m venv .venv

# Install dependencies
.venv/Scripts/pip install -r requirements.txt

# Run tests (unit only, no DB needed)
.venv/Scripts/pytest tests/unit/ -v

# Run dev server (requires .env with DATABASE_URL)
python scripts/run_dev.py
```

**Environment notes:**
- `JWT_SECRET_KEY` in `.env` must be 32+ bytes (PyJWT warns if shorter)
- DB-dependent tests require `TEST_DATABASE_URL` pointing to a running PostgreSQL instance
- Pure unit tests (smoke + pure function tests) pass without a live database
- `scheduler.py` contains emoji (🗑️) — always open with `encoding="utf-8"` in tests/scripts
