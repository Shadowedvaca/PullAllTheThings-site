# Work Management Standard

This document is the authoritative issue and GitHub Project workflow for this
repository. `AGENTS.md` and `CLAUDE.md` must both require AI agents to read it.
Repository-specific development, testing, CI/CD, and release instructions live
in `reference/development-and-release.md`.

## System of record

- GitHub issues are the source of truth for requirements, decisions,
  acceptance criteria, implementation evidence, and discussion.
- The private user Project **Solo Development** is the shared cross-repository
  planning view. Its fields organize work; they do not replace issue content.
- Use native GitHub parent/sub-issue relationships for hierarchy. A checklist
  may summarize children, but it is not a substitute for the native link.
- Repository labels describe repository-specific type or area when useful.
  Project Status, Priority, Size, Release, and Target date belong in Project
  fields rather than duplicated status labels.

## AI-created issue enrollment

When an AI agent creates an issue in this repository, issue creation is not
complete until the issue is enrolled in the private user Project
**Shadowedvaca / Solo Development** (`projects/7`). This requirement applies to
parents, children, bugs, discoveries, follow-up work, and newly identified
scope. The built-in Auto-add workflow is an optional safety net, not the
primary AI path.

The agent must complete this transaction:

1. Create the issue in the correct repository and capture its canonical URL.
2. Add that exact issue URL to **Solo Development** using an authenticated
   GitHub integration, API, or `gh project item-add`.
3. Verify that the issue appears in the Project exactly once.
4. Set Project Status to `Inbox` unless the user or an already approved active
   plan explicitly authorizes another lifecycle state.
5. Add the native parent/sub-issue relationship when the new issue is a child.
6. Report the issue URL, parent relationship when applicable, and confirmed
   Project enrollment to the user.

If Project enrollment or field assignment fails, do not delete and recreate the
issue, do not create a duplicate, and do not silently continue. Preserve the
issue as the source of truth, report the exact incomplete step and error, and
retry safely or hand the specific repair back to the user.

## Status lifecycle

| Status | Meaning |
|---|---|
| `Inbox` | Captured but not yet triaged. |
| `Backlog` | Accepted work that is not yet ready to begin. |
| `Ready` | Scoped, ordered where necessary, and ready to begin. |
| `In progress` | Implementation is actively underway. |
| `In review` | Automated validation is complete; review or approval is pending. |
| `Done` | Approved and complete at the delivery stage required by the issue or release slice. |
| `Not planned` | Declined, duplicate, obsolete, or intentionally not proceeding. |

Do not infer that moving a child to `Done` authorizes a parent merge or
production release. Parent and release state are evaluated separately.

## Work hierarchy

### Parent issues

A parent issue represents a durable outcome, feature area, or roadmap
direction. It provides context and the definition of done for the larger
outcome. A parent does not have to equal one immutable release.

Use these sections when applicable:

```markdown
## Goal
## Why this matters
## Child issues
## Scope
## Done when
## Guardrails
## Deferred
```

### Child issues

A child issue is the unit of implementation and review. Use these sections when
applicable:

```markdown
## Goal
## Parent
## Implementation scope
## Acceptance criteria
## Testing and validation
## Documentation and release notes
## Dependencies or guardrails
## Completion evidence
```

### Adding or changing children

Parents may gain explicit child issues when discovery, decomposition, a changed
external constraint, or previously unknown necessary work makes that useful.
This is expected planning, not a process failure.

When adding a child:

1. Explain why the work was not represented by the existing children.
2. State whether it is required for the current delivery slice or is later work.
3. Record scope, acceptance criteria, dependencies, and release impact.
4. Update the native parent/sub-issue relationship and any ordered child list.
5. Obtain explicit approval before implementing a material scope expansion.

Do not hide newly discovered work inside an unrelated child merely to preserve
the original issue list. Do not create a new parent when the work still belongs
to the same durable outcome.

## Delivery slices and branches

- A delivery slice is the explicitly selected, ordered set of children being
  implemented together.
- Use one shared branch and one cumulative draft pull request for that slice
  unless the repository-specific workflow says otherwise.
- A parent may span more than one delivery slice or release.
- Keep child commits focused so each child can be validated and approved
  independently.
- A failed manual check remains in the same child until fixed and revalidated.

## Approval boundaries

Approval is narrow:

- Approval to start a child authorizes that child only.
- Child approval may authorize the next ordered child when the active plan says
  so.
- Child approval does not authorize material scope expansion, merge, test
  deployment, production tagging, production deployment, server or DNS
  changes, GitHub environment changes, or secret changes.
- Merge and production release each require their own explicit approval when
  the repository workflow calls for them.

## Completion evidence

Before moving a child to `In review`, record:

- behavior implemented and important files changed;
- automated tests and quality checks run;
- CI result and development deployment result when applicable;
- documentation and release-note changes;
- manual verification instructions;
- risks, limitations, deviations, and follow-up work.

Move work to `Done` only after the approval and delivery stage required for that
item has completed. Close a parent only when its current definition of done is
satisfied; later children may remain under a durable parent if they are clearly
identified as later work.
