# Work Management Standard

This document is authoritative for Solo Development enrollment, issue hierarchy,
delivery slices, branches and pull requests, approvals, evidence, and closure.
`reference/development-and-release.md` owns quality, environments, promotion,
versioning, and release operations.

## System of record and enrollment

- GitHub issues are the source of truth for requirements, decisions, acceptance
  criteria, implementation evidence, and discussion.
- The private user Project **Shadowedvaca / Solo Development** (`projects/7`) is
  authoritative for workflow status and cross-repository planning.
- Use native GitHub parent/sub-issue relationships. A checklist may show order,
  but it does not replace the native relationship.
- Project fields own Status, Priority, Size, Release, and Target date. Repository
  labels may describe type or area; do not duplicate Project workflow in labels.

Every AI-created issue, including discovered or follow-up work, must be added to
Solo Development unless Mike explicitly marks it legacy or excluded. Creation is
one transaction:

1. Create the issue in the correct repository and retain its canonical URL.
2. Add that exact URL to Solo Development.
3. Verify it appears exactly once and set Status to `Inbox` unless an approved
   active plan authorizes another state.
4. Add and verify the native parent/sub-issue relationship when applicable.
5. Report the URL, relationship, enrollment, and status.

If any enrollment step fails, preserve the issue, do not create a duplicate,
and report and safely retry the exact incomplete step.

## GitHub CLI on Windows

- Run `gh auth` and all authenticated `gh` commands through the Windows host,
  outside any restricted execution sandbox. The Windows GitHub CLI credential
  store is the authoritative authentication context for this repository.
- Do not conclude that GitHub credentials are expired or invalid from a
  sandboxed `gh auth status`, `401`, or credential-store access failure. Retry
  the same read-only authentication check through the Windows host first.
- Use that Windows-host context for issue, Project, pull-request, workflow, and
  release operations that depend on `gh` authentication.

## Status lifecycle

| Status | Meaning |
|---|---|
| `Inbox` | Captured and awaiting triage. |
| `Backlog` | Accepted, but not ready to begin. |
| `Ready` | Scoped, ordered, and ready to begin. |
| `In progress` | Authorized implementation is active. |
| `In review` | Child-development evidence is complete and approval is pending. |
| `Done` | The issue's required approval and delivery stage are complete. |
| `Not planned` | Declined, duplicate, obsolete, or intentionally excluded. |

GitHub issue state and Project Status must be reconciled. A child becoming
`Done` does not authorize integration, test, production, or parent closure.

## Hierarchy and child expansion

A parent represents a durable outcome and definition of done. A child is a
bounded implementation and review unit. Use these sections when applicable:

```markdown
## Goal
## Why this matters
## Scope
## Done when / Acceptance criteria
## Guardrails / Dependencies
## Child issues / Parent
## Testing and validation
## Documentation and release notes
## Completion evidence
## Deferred
```

Discovery that belongs to the same parent outcome may become a new explicit
child. Record why it was missing, whether it is required for the active slice,
its acceptance criteria, dependencies, release impact, native relationship,
and ordered position. Obtain a material scope decision before implementing an
expansion that changes the approved outcome. Do not hide it inside an unrelated
child or create a new parent when the outcome is unchanged.

## Validation timing and integration cadence

Each parent or delivery slice records both controls:

- **User Validation Timing:** `Child`, `Parent`, or `Release`.
- **Integration Cadence:** `Parent` or `Child`.

`Parent` integration cadence is the default. Selected children share one
cumulative branch and draft pull request. Each child reaches its own Child
development complete approval; merge and test wait for the final selected child
and any Child- or Parent-timed human UI validation due before promotion. Do not
create one PR per child.

With `Child` cadence, each child is independently releasable and receives its
own branch/PR, merge/test promotion, and exact Mike-selected version.

User Validation Timing controls only UI behavior a person must validate:

- `Child`: after AI-executable work and the Child development complete approval,
  validate the prepared development artifact before another child or Promotion
  to test depends on that result.
- `Parent`: after every selected child's Child development complete approval,
  validate the cumulative development artifact before Promotion to test.
- `Release`: after Promotion to test, validate the exact test candidate before
  Promotion to production. Release-timed validation is not due before test and
  cannot be used to delay or condition the Promotion to test approval.

Automated checks, API checks, database checks, and health checks are evidence,
not human approval gates. A failed manual check stays with the same child until
fixed and revalidated.

## Branch and pull-request handling

- A delivery slice is the explicit ordered set of children being delivered
  together under its Integration Cadence.
- Start from the approved integration base. Use one focused branch and one
  cumulative draft PR per slice.
- Keep child commits and evidence distinguishable even on a shared branch.
- Direct integration commits are not the normal path. Merge through the approved
  PR only at the Promotion to test gate.
- Keep the PR description, issue links, release note, validation evidence,
  deviations, and known limitations current throughout the slice.

## Authorization and approval gates

Routine in-scope work proceeds without repeated permission: inspection, coding,
tests, lint/format/static checks, builds, API/database/health checks,
documentation, cumulative release-note maintenance, branch updates, evidence,
and draft PR preparation.

The happy-path approval gates are only:

1. **Child development complete:** implementation, automated checks,
   documentation, cumulative release note, and evidence are complete.
2. **Manual human UI validation:** only when due under User Validation Timing
   and only for behavior a person must validate. Child-timed validation follows
   that child's completion approval; Parent-timed validation follows all selected
   child approvals; Release-timed validation follows Promotion to test.
3. **Promotion to test:** the slice, all selected Child development complete
   approvals, and only Child- or Parent-timed validation due before test are
   complete, and the PR is ready. One approval authorizes merge to `main` and its
   test deployment. Release-timed validation is not a prerequisite.
4. **Promotion to production:** test evidence and release reconciliation are
   complete, including any Release-timed UI validation on the exact test
   candidate. One approval authorizes the exact Mike-selected production tag
   and production CI/CD.

Stop elsewhere only for a genuine question, material scope decision,
unexpected risk, missing authority, uncovered destructive action, security
concern, or blocker. Approval is narrow: no gate authorizes production, secrets,
GitHub environment rules, DNS, infrastructure, or destructive recovery unless
that exact action is included.

## Evidence and closure

Before requesting Child development complete approval and moving a child to
`In review`, record:

- implemented behavior and important files;
- automated tests, lint/format/static checks, build, and CI results;
- development deployment identity and checks when applicable;
- database/migration and health evidence when applicable;
- documentation and cumulative release-note changes;
- manual UI instructions and timing, or why none are required;
- risks, limitations, deviations, and explicit follow-up issues.

After approval, mark the child `Done` only if its required delivery stage is
complete. Close a parent only when its current definition of done is satisfied,
all required children and evidence are reconciled, and deferred work is explicit.
