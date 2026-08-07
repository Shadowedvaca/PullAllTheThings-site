# Pull All The Things Unreleased

## Highlights

- Establishes the repository foundation for owner-selected versions, cumulative release notes, pull-request validation, and exact-commit deployment evidence.

## Fixes/Changes

- Uses root `VERSION` as the application version authority and reports version, environment, and commit identity through FastAPI and `/api/health`.
- Adds repository-owned release-note templates, validation, tests, and a post-production GitHub Release contract.
- Aligns Solo Development, approval, validation-timing, integration-cadence, promotion, rollback, and evidence instructions.
- Removes the documented hotfix path that permitted production promotion before test completed.
- Adds a repository-owned Production readiness interlock that defaults to blocked, plus exact-tag-SHA provenance checking against a successful test deployment before Production can proceed.
- Caps the framework and test-runner dependencies to the compatibility lines exercised by the current application while a future upgrade is validated explicitly.
- Reconciles stale test assertions with already-shipped behavior: the Icy Veins `healing` URL slug, the compact Battle.net character lock presentation, and the configured 43,200-minute (30-day) JWT lifetime. These are baseline corrections, not new product approvals; the JWT lifetime still requires Mike's explicit product/security acceptance.

## Validation

- Release-contract tests, runtime identity tests, documentation checks, Python static checks, the application test suite, and container build validation are required before integration.

## Deployment/Migrations

- No database migration is included. Deployment workflows require an exact resolved commit and verify the reported version and commit after startup.
- Mike must select the exact release version before these notes can be reconciled into a versioned note and production can be tagged.

## Rollback

- Before production, revert this documentation and workflow slice. After a future release, redeploy the prior immutable validated tag when its database contract remains compatible; restore a verified pre-deployment backup when a migration is not backward compatible.

## Known Limitations

- Production remains deliberately unavailable until required foundation issues #54 and #55 implement and verify GitHub environment and branch protections, strict SSH trust and isolated credentials, migration/backup safety, downgrade/restore rehearsal, and bounded rollback. These are mandatory blockers, not optional follow-ups.
- The repository exact-SHA test-provenance preflight is implemented but unexecuted in this slice. It must be safely evidenced before Production readiness can be enabled.
- The existing 30-day JWT lifetime remains pending Mike's explicit product/security acceptance; this slice does not change the runtime value.
- Full PostgreSQL-backed PR validation currently fails on 46 pre-existing stale integration contracts after 2,054 tests pass. Issue #57 is a mandatory integration blocker for this slice; the failures may not be skipped, xfailed, or treated as product/security approvals merely to make CI green.
