# Development and Release Standard

This document is authoritative for environment roles,
deployment and promotion, version authority, release notes, migrations, health,
rollback, and release evidence. `reference/work-management.md` owns issue and
approval workflow. `reference/testing-and-validation.md` and
`reference/testing-profile.md` own test definitions, applicability, coverage,
browser/E2E, and the concrete quality commands.

## Development loop and quality gates

1. Read the active parent, ordered children, repository references, and current
   cumulative release note. Confirm User Validation Timing and Integration
   Cadence; default Integration Cadence to `Parent`.
2. Work on the slice branch and cumulative draft PR. Move only active work to
   `In progress`.
3. Implement behavior, tests, documentation, and `docs/releases/UNRELEASED.md`
   together. Do not wait until promotion to reconstruct evidence.
4. Consult the testing profile, run focused checks while iterating, then its
   complete applicable local gate, including coverage and browser/E2E. Database-
   dependent checks require an isolated non-production database.
5. PR CI enforces the profile's release, migration, full-suite, overall and
   changed-line coverage, browser/E2E, static, Compose, and clean-image gates. A
   skipped, unavailable, or failing required check is a recorded gap, not success.
6. If development deployment is useful, dispatch `deploy-dev.yml` with the
   explicit branch. The workflow resolves it to one SHA, deploys detached at
   that SHA, and verifies version, commit, environment, database connectivity,
   health, and migration heads.
7. Record evidence defined in `reference/work-management.md` and stop at the
   Child development complete gate. If User Validation Timing is `Child`, its
   human UI check occurs only after that approval on the prepared development
   artifact. Parent-timed UI checks occur after all selected child approvals on
   the cumulative development artifact. Release-timed UI checks occur only after
   Promotion to test on the exact test candidate.

## Environment roles and promotion

| Environment | Role | Source and authorization | Required evidence |
|---|---|---|---|
| Development | Isolated feedback for the active branch. | Manual workflow dispatch for an explicit branch; routine once in slice scope. | Resolved SHA, version, environment, DB/health, migration head, workflow result, and applicable UI/API checks. |
| Test | Integration and release-candidate validation. | Exact approved `main` commit produced by the Promotion to test approval after any Child- or Parent-timed UI validation. Release-timed validation is not due yet. | PR/CI result, deployed SHA, version, environment, DB/health, migration head, validation due before test, and deviations. |
| Production | Live users and data. Currently repository-blocked by required foundation work. | Exact approved immutable `prod-vX.Y.Z` tag after successful test deployment of the same SHA, any Release-timed UI validation, and Promotion to production approval. | Tag and SHA, exact-SHA successful test workflow record, main ancestry, selected version, curated note, workflow result, migration/health evidence, production smoke checks, rollback readiness, and GitHub Release URL. |

The test workflow may deploy only the approved `main` integration commit. The
production workflow may deploy only the tag target, must prove the tag equals
`prod-v` plus `VERSION`, must prove the target is contained in `main`, and must
find a successful `Deploy to Test` push run whose `head_sha` is exactly the tag
target. Main ancestry alone is insufficient.
Hotfix urgency does not bypass tests or either promotion approval. If required
evidence cannot be produced, stop and document the blocker and risk.

## Version authority

- Root `VERSION` is the single repository version source in canonical `X.Y.Z`
  form. Runtime metadata, `/api/health`, release validation, notes, tags, and
  GitHub Releases derive from it.
- Mike alone selects the exact version. AI must never infer, calculate,
  increment, or substitute it from branch type or change size.
- After Mike supplies a value, apply that exact value to `VERSION`, reconcile
  `docs/releases/UNRELEASED.md` into `docs/releases/X.Y.Z.md`, reset cumulative
  notes for later work, and verify every version surface agrees.
- The current `0.24.2` authority is reconciled from the existing immutable tag
  and application history; it is not a newly selected release for this slice.

## Cumulative release-note lifecycle

`docs/releases/UNRELEASED.md` is the cumulative record for work not yet assigned
an owner-selected release version. `docs/releases/TEMPLATE.md` defines required
sections: Highlights, Fixes/Changes, Validation, Deployment/Migrations,
Rollback, and Known Limitations.

Each child updates the same unreleased note from actual behavior and evidence.
At release reconciliation, Mike selects the exact version; copy and curate the
cumulative content into `docs/releases/X.Y.Z.md`, update `VERSION`, and leave no
shipped claim in `UNRELEASED.md`. `python scripts/validate_release.py` rejects
malformed versions, missing/mismatched headings and sections, placeholders,
and credential-shaped content. Do not put secrets or internal connection
details in release notes.

No GitHub Release currently exists for historical tags. For future production
promotions, `deploy-prod.yml` calls the isolated `publish-release.yml` only after
the exact tag deploy and health/migration verification succeed. The publisher
creates or updates the Release for that immutable tag using only the matching
versioned curated note. A failed deployment must not publish a Release.

## Migrations, health, and rollback

The container entrypoint runs `alembic upgrade head` before the application.
For every environment with a database change:

- before deployment: review upgrade/downgrade behavior, data compatibility,
  lock/downtime risk, and confirm a usable environment-specific backup;
- after deployment: verify `/api/health` reports `ok`, DB `connected`, expected
  environment/version/commit, and verify `alembic current --check-heads`;
- record the starting and ending revision and any data or manual checks.

Rollback is a decision, not a blind command. For code-only compatible changes,
redeploy the prior immutable validated tag/SHA. Never move or reuse a tag. For
non-backward-compatible migrations, stop writes as required, use the documented
and verified backup/restore path in `docs/BACKUPS.md`, and reconcile code and DB
to a compatible state. If recovery needs changed code or migrations, Mike
selects a new exact version and the normal test/production gates apply.

Development and test use their isolated databases and evidence. Production
rollback or restore is destructive live-data work and requires exact authority.
Do not perform a deployment or restore merely to prove documentation.

## Current foundation boundaries

Implemented repository controls include PR validation, canonical version and
release-note validation, exact-SHA checkout and runtime reporting, test-from-main,
production tag/version/main-ancestry checks, an exact-SHA successful-test-run
preflight, post-deploy health/migration checks, and post-success GitHub Release
publication.

Issue #57 reconciles the pre-existing PostgreSQL-backed test baseline exposed by
the new workflow. Pull-request run `31226039583` passed the migration chain,
2,099 tests, JavaScript syntax, Compose validation, and a clean image build at
commit `fde6b46`. The changes correct test isolation and stale assertions to
match existing runtime behavior; they do not newly approve product or security
contracts. Future failures remain blockers, not waivers or permission to skip,
remove, or mark affected tests expected to fail. Dependency ranges are capped
to the FastAPI, Starlette, pytest, and pytest-asyncio compatibility lines
exercised by this repository; upgrades need their own successful validation
evidence.

Production is intentionally fail closed. `.github/production-readiness.json`
sets `production_enabled` to `false`, and `deploy-prod.yml` runs
`scripts/validate_production_readiness.py` before test-provenance lookup, secrets,
SSH, or deployment. A tag that matches `VERSION` and points into `main` therefore
cannot currently reach Production. Enabling requires a reviewed repository
change that sets every required control to `implemented_and_verified` and then
explicitly sets `production_enabled` to `true`; this slice does neither.

The following are mandatory Production blockers, not optional follow-ups:

- issue #54: implement and evidence migration safety, environment-specific
  backup verification, bounded rollback, and downgrade/restore rehearsal;
- issue #55: implement and evidence strict SSH host trust, isolated environment
  credentials, GitHub environment protections, and required branch/PR checks.

The exact-SHA test provenance lookup is implemented in the repository workflow,
but has not been executed by this slice. Production must remain disabled until
#54 and #55 are complete, the remote protections and host controls are verified,
the provenance preflight is validated safely, and Mike explicitly approves the
readiness-file change. Do not claim any blocked control exists merely because a
workflow or document describes its required future state.

The shipped 43,200-minute (30-day) JWT lifetime is also awaiting Mike's explicit
product/security acceptance. The test assertion in this slice records existing
runtime behavior only; it does not approve or establish that lifetime as a new
security contract. Do not change the runtime value as part of this foundation
slice.
