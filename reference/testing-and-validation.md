# Testing and Validation Standard

This document is authoritative for test-layer definitions, applicability,
coverage governance, regression expectations, test isolation, flaky-test
handling, and evidence. `reference/testing-profile.md` supplies the concrete
PATT commands and floors. `reference/development-and-release.md` owns environment
promotion and release evidence.

## Required test assessment

Before implementation, classify the change against every layer below. A layer
is either applicable and executed, or not applicable with a concrete reason.
Unavailable required coverage is a visible gap with an owning issue; it is not
a pass.

| Layer | Purpose |
|---|---|
| Static | Catch syntax, import, formatting, lint, configuration, workflow, and contract defects without executing product behavior. |
| Unit | Exercise one function, class, or module with external boundaries replaced by controlled fakes. |
| Integration | Exercise real collaboration among application components, including PostgreSQL where persistence is part of the contract. |
| Migration/database | Prove schema creation or migration reaches the expected heads and preserves the asserted data contract in an isolated database. |
| Provider boundary | Verify request, response, timeout, retry, and error behavior at Discord, Blizzard, Raider.IO, Warcraft Logs, email, and other external boundaries without using live credentials. |
| Regression | Reproduce a defect or previously broken contract and fail without the associated correction. |
| Coverage | Measure executed source statements and changed source lines; it supplements behavioral assertions and never replaces them. |
| Automated UI/E2E | Drive a real browser against a live test application to protect selected user journeys. |
| Deployed smoke | Probe the exact deployed SHA for identity, health, database, migration, and critical environment behavior. |
| Manual human UI | Collect a person's judgment about presentation or interaction only when due under User Validation Timing. |

Automated UI/E2E is automated evidence. It is never a substitute for required
manual human UI validation and never creates an extra human approval gate.

## Applicability and test design

- Every behavior change receives the narrowest useful automated protection at
  the lowest layer that proves the contract, plus broader integration or browser
  coverage when the risk crosses boundaries.
- Every defect fix includes a regression test that fails on the defective
  behavior. If automation is genuinely impractical, the active issue records
  why, the repeatable manual evidence, the risk, and an approved follow-up.
- Database queries, transactions, migrations, and persistence semantics require
  isolated PostgreSQL coverage. SQLite or a mock is not equivalent evidence.
- Provider integrations use representative recorded or constructed payloads and
  bounded fakes. Tests do not call live guild, Discord, Blizzard, email, or other
  production services.
- User-facing JavaScript or navigation changes require the relevant syntax,
  service/API, and browser journey checks defined by the profile.

## Coverage governance

The profile records measured overall and changed-line baselines. CI fails when
either floor is missed.

- Floors ratchet upward when sustained evidence supports an increase.
- A floor must not be lowered merely to make a change pass. Lowering requires
  Mike's explicit approval in an issue that records the measured cause, affected
  risk, remediation owner, target, and restoration plan.
- Exclusions must describe generated, platform-only, or otherwise non-executable
  code. Excluding difficult product code to improve a number is prohibited.
- New and changed behavior is expected to meet the changed-line floor and have
  meaningful assertions even when the repository-wide legacy floor is lower.
- Coverage generated with unavailable database layers is local diagnostic
  evidence. The hosted PostgreSQL run is the integration authority.

## Test data, isolation, and secrets

- Tests use synthetic identities, content, credentials, tokens, and provider
  payloads. Production exports and real user data are prohibited.
- Production credentials, tokens, secret material, and service accounts are
  prohibited in every local and hosted test layer.
- PostgreSQL tests use the dedicated test URL, create only required schemas, and
  clean or roll back state between tests. Migration rehearsal uses a separate
  isolated database from application integration tests.
- CI placeholder credentials must be obviously non-secret and usable only inside
  the ephemeral job.
- Logs, screenshots, traces, coverage reports, and failure artifacts must be
  reviewed for credential or personal-data exposure before they become evidence.

## Flaky tests and failures

- A required failing test blocks its gate. Re-running may establish whether a
  failure is intermittent but does not convert failure into success.
- Quarantine, retry, skip, or expected-failure treatment requires an owning issue,
  bounded reason, affected gate, and repair target. Critical security, migration,
  promotion, and smoke checks may not be quarantined.
- Preserve the first useful failure output and the final passing run. Record gaps
  such as unavailable tools, skipped tests, or missing environments explicitly.

## Evidence at child completion

The child evidence records applicable layers, exact commands, result counts,
coverage percentages, CI run and SHA, artifacts, database/migration context,
deployed smoke identity when used, manual-validation timing, and all deviations.
Only executed checks may be described as passing.
