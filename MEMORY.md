# PATT Repository Memory

This file is a durable routing aid, not a source of transient project status.
GitHub issues and the Solo Development project own current work, decisions,
approvals, and delivery state. Release evidence belongs in the applicable issue,
pull request, and release record.

## Instruction precedence

For repository work, read and apply these sources in order:

1. `AGENTS.md` or the byte-identical `CLAUDE.md` entry point.
2. `reference/ai-context.md` for durable architecture and repository constraints.
3. `reference/work-management.md` for issue, cadence, approval, and evidence rules.
4. `reference/testing-and-validation.md` for the repository-wide testing policy.
5. `reference/testing-profile.md` for PATT-specific tools, commands, floors, and
   environment expectations.
6. `reference/development-and-release.md` for environments, versions, promotion,
   release notes, migrations, health, and rollback.
7. Additional canonical references named by those documents for the active work.

Higher-level user instructions select goals, scope, versions, cadence, and
validation timing. They do not silently waive repository safety, evidence, or
promotion controls. Report a material conflict before changing state.

## Durable boundaries

- Mike alone selects exact versions and authorizes the documented promotion gates.
- Production credentials, user data, and databases are never test inputs.
- Required failures remain visible; do not weaken, skip, or simulate a gate to
  obtain a passing result.
- Do not store secrets, personal data, temporary branch state, issue status,
  deployment identity, or copied conversation history in this file.
