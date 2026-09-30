# Pull All The Things Unreleased

## Highlights

- Development and Test deployments now coordinate with other applications on
  their shared hosts through one bounded host lock.

## Fixes/Changes

- Pause automatic player-rank and Discord guild-role reconciliation in the
  scheduled Blizzard and Discord sync pipelines while rank-source accuracy is
  investigated in issue #51. Member sync and mismatch detection continue.
- Add repository-owned shared-host admission around both preparation and
  activation, including disk, swap, and memory headroom checks before mutation.
- Keep Production dedicated and unchanged; no custom host helper or centralized
  deployment service is introduced.

## Validation

- Add a scheduler regression check that both scheduled syncs retain mismatch
  detection without invoking rank reconciliation.
- Add deployment-contract coverage for lock identity, phase boundaries,
  resource admission, exact-SHA wrapper transfer, and prohibited global cleanup.

## Deployment/Migrations

- No migration is included. Development and Test need the standard `flock`
  utility and the existing 2 GiB swap configuration; the repository installs no
  host component.

## Rollback

- Restore the two scheduler reconciliation calls after issue #51 establishes
  authoritative rank data and verifies promotion and demotion behavior.
- Reverting the workflow and repository-owned admission script removes PATT's
  participation in the shared lock; no server-side helper needs removal.

## Known Limitations

- Automatic changes to website player ranks and Discord guild rank roles remain
  paused until issue #51 is resolved; officers must review rank mismatches.
