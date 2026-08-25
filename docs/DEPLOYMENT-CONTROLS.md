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

## Live audit and verified cutover

Read-only audit on 2026-08-25 found:

- only the unrelated `github-pages` environment existed;
- `main` had no branch protection and the repository had no rulesets;
- GitHub Actions allowed all actions and did not require SHA pinning;
- `DEV_HOST`, `TEST_HOST`, `PROD_HOST`, and one shared `DEPLOY_SSH_KEY` existed at
  repository scope;
- no Development, Test, or Production environment secrets or variables existed.

Mike explicitly approved the live GitHub, SSH-host, key, secret, and legacy-secret
cutover on 2026-08-25. Verification after the approved change established:

- `main` requires a pull request, the strict `Quality, migrations, tests, and
  build` check, resolved conversations, and admin enforcement; it requires zero
  additional GitHub approving reviews and disallows force pushes and deletion;
- Actions remain enabled with the existing allow-all policy and now require full
  commit-SHA action pins;
- Development allows explicitly dispatched refs, Test allows protected branches
  only, and Production allows only tags matching `prod-v*`; none adds a reviewer
  or wait timer;
- every environment has only the expected deployment secret names and
  `DEPLOY_USER=root`; values were neither read back nor recorded;
- three newly generated ED25519 deployment keys connected successfully with
  strict trusted-host verification and are authorized only on their matching
  hosts;
- the shared `github-actions-deploy` key fingerprint was removed from all three
  active `authorized_keys` files without affecting Mike's administrative or SATT
  keys;
- the repository-level `DEV_HOST`, `TEST_HOST`, `PROD_HOST`, and shared
  `DEPLOY_SSH_KEY` secrets were deleted;
- mode-`600` recovery copies named
  `/root/.ssh/authorized_keys.pre-patt-55-20260825` remain on each host. They are
  not active authorization files and provide bounded rollback evidence.

Exact secret values, private keys, host-key material, and administrative keys are
not present in repository, issue, PR, or release records.

The production-preflight lookup was safely exercised without deployment against
successful Test run `30419325704` for exact SHA
`d35786b9910707109395abad24ddd06d64bcc08c`; the validator selected that run and
did not infer evidence from main ancestry alone.

Production remains repository-disabled. Live controls and provenance are now
verified, but #55 still requires Child development complete approval, all selected
children then require Parent-timed human validation and integration preflight,
and the separate Test and Production promotion approvals remain mandatory.
