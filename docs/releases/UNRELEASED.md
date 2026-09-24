# Pull All The Things Unreleased

## Highlights

- Development and Test deployments now coordinate with other applications on
  their shared hosts through one bounded host lock.

## Fixes/Changes

- Add repository-owned shared-host admission around both preparation and
  activation, including disk, swap, and memory headroom checks before mutation.
- Keep Production dedicated and unchanged; no custom host helper or centralized
  deployment service is introduced.

## Validation

- Add deployment-contract coverage for lock identity, phase boundaries,
  resource admission, exact-SHA wrapper transfer, and prohibited global cleanup.

## Deployment/Migrations

- No migration is included. Development and Test need the standard `flock`
  utility and the existing 2 GiB swap configuration; the repository installs no
  host component.

## Rollback

- Reverting the workflow and repository-owned admission script removes PATT's
  participation in the shared lock; no server-side helper needs removal.

## Known Limitations

- No additional limitations have been accepted after the 0.24.4 release candidate.
