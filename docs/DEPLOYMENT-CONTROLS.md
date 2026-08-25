# GitHub and SSH Deployment Controls

This document records the non-secret deployment enforcement contract for PATT.
`reference/development-and-release.md` remains authoritative for approvals and
promotion. `.github/deployment-controls.json` is the machine-validated desired
GitHub configuration.

## Repository controls

- Every external GitHub Action is pinned to a full commit SHA. Repository Actions
  settings must also enforce full-SHA pinning.
- Deployment uses the runner's native OpenSSH client. No third-party SSH action is
  used.
- `deploy/configure-deployment-ssh.sh` accepts an environment-scoped private key
  and known-hosts record, validates them without printing their contents, and
  never learns trust with `ssh-keyscan` during deployment.
- `deploy/run-strict-ssh.sh` uses an empty SSH configuration plus batch mode,
  `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, and the supplied known-hosts
  file. A missing or mismatched host key fails before remote commands run.
- Development, Test, and Production use the same secret names in separate GitHub
  environment scopes: `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, and `DEPLOY_SSH_KEY`.
  `DEPLOY_USER` is an environment-scoped variable. Values must never be copied
  into issues, logs, manifests, or repository files.
- Each private key must be unique to one environment and authorized only on that
  environment's host. Removing the former repository-level shared values is part
  of live cutover verification.

## GitHub enforcement contract

The happy path deliberately adds no GitHub reviewer beyond Mike's documented
workflow approvals:

| Control | Required setting |
|---|---|
| `main` | Pull request required; zero additional GitHub approving reviews; strict required check `Quality, migrations, tests, and build`; admins included; conversations resolved; force pushes and deletion disabled |
| `development` | Environment exists; all explicitly dispatched branch refs allowed; zero reviewers and zero wait timer |
| `test` | Environment exists; protected branches only; zero reviewers and zero wait timer |
| `production` | Environment exists; custom tag policy `prod-v*` only; zero reviewers and zero wait timer |
| Actions | Enabled; current allow-list policy retained; full-SHA pinning required |

The zero-reviewer rule is intentional: the Solo Development handoff and promotion
approvals are already recorded outside GitHub's pull-request review primitive.
Requiring another GitHub review would invent an extra happy-path approval. Branch,
status, environment, exact-SHA provenance, and repository-readiness gates remain
technical blockers.

## Live audit and cutover boundary

Read-only audit on 2026-08-25 found:

- only the unrelated `github-pages` environment existed;
- `main` had no branch protection and the repository had no rulesets;
- GitHub Actions allowed all actions and did not require SHA pinning;
- `DEV_HOST`, `TEST_HOST`, `PROD_HOST`, and one shared `DEPLOY_SSH_KEY` existed at
  repository scope;
- no Development, Test, or Production environment secrets or variables existed.

That state does **not** satisfy issue #55. Applying the desired GitHub settings,
creating three independently authorized host keys, obtaining known-host records
through a separately trusted channel, installing each public key on only its
matching host, populating the environment-scoped values, and deleting the legacy
repository secrets are externally visible security/infrastructure changes. They
require Mike's explicit authority and verified evidence. Secret values and public
key material are not recorded here.

Production remains repository-disabled until live configuration is verified,
exact-SHA Test provenance is safely exercised, the readiness record is reconciled
in review, and Mike provides the later promotion approvals.
