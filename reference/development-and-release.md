# Development and Release Standard

This document is authoritative for progressing repository work through
implementation, review, integration, and release. `reference/work-management.md`
defines issue hierarchy, Project fields, status, and approval boundaries.
Prompt workbooks and future pipelines may supply invocation context, but they
must not override these repository instructions.

## Delivery loop

1. Read the parent, selected ordered children, and relevant repository
   references before changing code.
2. Use one shared branch and cumulative draft pull request for the current
   delivery slice.
3. Move only the active child to `In progress`.
4. Implement the child, update tests and documentation, and run focused checks
   while iterating.
5. Run the repository's complete local validation before requesting review.
   The current Python suite is `pytest tests/ -v`; use the repository virtual
   environment when present.
6. Push the tested commit. When development deployment is required, dispatch
   `deploy-dev.yml` for the explicit branch and validate the deployed behavior.
7. Record test, CI, development deployment, manual verification, and deviation
   evidence. Move the child to `In review` and wait for approval.
8. A failed manual check remains in the same child until fixed and revalidated.
9. After approval, move the child to `Done` and continue only with the next
   approved child.

## Integration and release

After all children selected for the slice are approved, review the complete
diff, tests, documentation, and release record; update the cumulative pull
request; and obtain explicit merge approval. Merge through the pull request.
A push to `main` deploys the integration commit to test through
`deploy-test.yml`; verify test before considering a production release.

Production tags use `prod-vX.Y.Z`. Obtain separate, explicit production-release
approval immediately before creating or pushing a tag. `deploy-prod.yml`
deploys only the tag-selected commit. Report the tag, SHA, workflow result,
health checks, and manual production verification.

Hotfixes keep the same evidence and approval boundaries. Any shortened test
path must be explicitly proposed, approved, documented with risk, and followed
by reconciliation of `main` and test.

Do not modify production, infrastructure, DNS, GitHub environments, or secrets
without specific approval for that action.
