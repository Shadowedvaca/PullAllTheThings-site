# PATT Testing Profile

This profile implements `reference/testing-and-validation.md` for the PATT Guild
Platform. Commands run from the repository root. CI uses Python 3.11 and
PostgreSQL 16; local results from another runtime are diagnostic and must name
that runtime.

## Suite inventory and ownership

| Layer | Location or command | Required placement | Evidence owner |
|---|---|---|---|
| Static | `python -m compileall -q src scripts tests`; changed-file `ruff check --select E9,F63,F7,F82`; `ruff format --check` for new Python; `node --check` for browser and companion JavaScript | Local complete gate and PR CI | Implementer; PR workflow enforces |
| Release/readiness/deployment contracts | `python scripts/validate_release.py`; `python scripts/validate_production_readiness.py --configuration-only`; `python scripts/validate_deployment_controls.py`; Bash syntax for deployment helpers | Every PR | Implementer; PR workflow enforces |
| Unit/provider boundary | `python -m pytest tests/unit -q` | Local iteration and PR CI | Implementer |
| Integration | `python -m pytest tests/integration -q` with `TEST_DATABASE_URL` | PR CI PostgreSQL service; local only with an isolated database | Implementer |
| Regression | `python -m pytest tests/regression -q` with `TEST_DATABASE_URL` | PR CI and for affected local work | Implementer |
| Migration/database and recovery | Fresh upgrade/head plus `python scripts/rehearse_database_recovery.py` against isolated PostgreSQL: custom archive inspection, isolated restore/fingerprint, one-revision downgrade/re-upgrade, and returned head | PR CI; deployed head check in each environment | Implementer; deployment workflow enforces |
| Overall coverage | Full unit, integration, and regression command below | PR CI | PR workflow enforces |
| Changed-line coverage | `diff-cover artifacts/coverage.xml --compare-branch=<base-sha> --fail-under=72` | PR CI against the actual PR base SHA | PR workflow enforces |
| Automated UI/E2E | `python -m pytest tests/e2e -q --tracing=retain-on-failure --screenshot=only-on-failure --output=artifacts/playwright` | PR CI Chromium; locally when browser runtime is installed | Implementer; PR workflow enforces |
| Compose/image | All four Compose definitions, clean production image build, and that image booted against a fresh ephemeral PostgreSQL 16 database with exact version/environment/commit/DB/heads checks | PR CI | PR workflow enforces |
| Deployed smoke | Environment-specific checks below | Exact-SHA development/test/production workflow | Deployment workflow enforces; implementer records |

The test tree currently contains unit tests for services and provider boundaries,
PostgreSQL-backed integration tests, a platform regression suite, and a Playwright
browser suite. Tests that explicitly require PostgreSQL may skip locally when no
isolated database is available; they must execute in PR CI.

Recovery rehearsal uses only synthetic CI data. Restore targets must use the
bounded `patt_recovery_` database namespace. The retained custom archive and
stable fingerprint prove only the exact migration and restore path exercised by
that run; they do not authorize or simulate a live restore.

## Coverage baselines and ratchet

The initial baseline was measured on child #59 from cumulative PR #56 at commit
`906b752` using the portable suite without PostgreSQL: 18,633 statements, 6,973
covered, **37% overall statement coverage**; 1,932 passed and 201 database tests
skipped. The same report measured **72% changed-line coverage** against `main`.
Hosted PostgreSQL execution is expected to meet or exceed these conservative
floors.

PR CI runs:

```text
python -m pytest tests/unit tests/integration tests/regression -q \
  --cov=src/guild_portal --cov=src/sv_common --cov=companion_app \
  --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml \
  --cov-fail-under=37
diff-cover artifacts/coverage.xml --compare-branch=<base-sha> --fail-under=72
```

The floors are `37` overall and `72` for changed lines. They are repository
controls, not quality targets. Increase them as coverage improves. Any decrease
uses the exception process in `reference/testing-and-validation.md`.

## Browser critical journeys

The required browser layer begins with a real Chromium journey through the public
login and registration entry points, static JavaScript execution, client-side
password feedback, and server-side validation. It runs against an ephemeral live
test application with external services disabled and synthetic inputs.

Changes affecting these areas extend the suite in the same child:

- login, logout, cookie, JWT, or authorization behavior: successful member login,
  logout, expired/revoked session, and protected-page denial;
- officer/admin navigation or authorization: representative read-only officer page
  plus denial for an ordinary member;
- campaigns/voting: eligible vote submission and duplicate/ineligible denial;
- member settings or character ownership: representative view and authorized write;
- setup or onboarding: first-run redirect and a bounded synthetic setup path.

The authentication browser journey proves successful member login, the issued
cookie contract, authenticated identity, logout revocation, and protected-route
denial after logout. Browser tests do not use live Discord, Blizzard, production
identities, or production data.

## Provider boundaries

Discord, Blizzard, Raider.IO, Warcraft Logs, Wowhead, Archon, Icy Veins, Method,
email, Google, and Raid-Helper behavior is tested with controlled clients and
representative synthetic payloads. Network calls are disallowed in the ordinary
test suite unless a separately authorized non-production contract test records
its endpoint, timeout, retry behavior, and safe credentials.

## Deployed smoke profile

Every deployed check proves the candidate identity before product assertions:

1. `/api/health` returns `ok`, database `connected`, expected environment,
   exact `VERSION`, and exact deployed commit.
2. `alembic current --check-heads` succeeds against that environment's database.
3. The workflow records its resolved SHA and deployment result.
4. Development checks only the explicitly dispatched branch SHA.
5. Test checks only the approved `main` integration SHA.
6. Production additionally proves the immutable tag matches `VERSION`, belongs to
   `main`, and has a successful exact-SHA Test deployment before smoke checks.
7. The remote deployment runs from the checked-in script with child-process stdin
   detached. The workflow uses `pipefail` and requires the exact
   `PATT_DEPLOYMENT_COMPLETE` line emitted only after backup, health identity,
   migration-head, and atomic active-SHA verification; checkout/build output or
   remote end-of-file is not completion evidence.

Feature-specific API or UI smoke checks are added when the changed behavior needs
deployed infrastructure. Production smoke never mutates real data merely to prove
a release.

For Child- or Parent-timed manual UI validation, run the Development workflow
for the exact due-validation branch head before issuing the checklist. Record the
successful workflow URL and confirm `/api/health` reports Development, the exact
`VERSION`, exact commit, connected database, and current Alembic head. The human
validator receives an already prepared URL; asking them to discover that
Development is stale is a process failure, not manual-validation evidence.

## Complete local gate

Run the applicable focused tests while iterating, then before child completion:

```text
python scripts/validate_release.py
python scripts/validate_production_readiness.py --configuration-only
python scripts/validate_deployment_controls.py
python -m compileall -q src scripts tests
python -m pytest tests/unit tests/integration tests/regression -q --cov=src/guild_portal --cov=src/sv_common --cov=companion_app --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml --cov-fail-under=37
diff-cover artifacts/coverage.xml --compare-branch=<integration-base> --fail-under=72
python -m pytest tests/e2e -q --tracing=retain-on-failure --screenshot=only-on-failure --output=artifacts/playwright
```

Also run changed-file Ruff checks, JavaScript syntax, workflow parsing or
`actionlint` when installed, documentation links when installed, Compose parsing,
and a clean image build. An unavailable local database, browser, Docker engine, or
optional checker is a recorded local gap; PR CI remains required.
