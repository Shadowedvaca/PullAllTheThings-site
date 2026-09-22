# Pull All The Things Unreleased

## Highlights

- Development and Test deployments now apply bounded, fail-closed backup
  retention only after all deployment success gates pass.

## Fixes/Changes

- Retain the newest three complete backup pairs in Development and seven in
  Test; Production backups are never automatically pruned.
- Refuse pruning when backup evidence is orphaned, temporary, malformed, empty,
  or does not match its manifest identity, and report exact selected paths.

## Validation

- Added unit coverage for retention selection, dry-run reporting, deletion
  boundaries, orphan/incomplete evidence, and deployment ordering.

## Deployment/Migrations

- No migration is included. The retention helper runs only after health,
  identity, migration-head, and active-SHA verification.

## Rollback

- If retention refuses to run, preserve all backup evidence and investigate the
  exact directory; do not bypass the check with broad cleanup.

## Known Limitations

- Existing pre-deployment backups outside the exact Development or Test PATT
  directories are not managed by this policy.
