# PATT Database Backup and Recovery

`reference/development-and-release.md` is authoritative for promotion,
rollback, and destructive-action approval. This runbook describes the
repository-enforced backup contract and the evidence an operator needs to make
a recovery decision. It does not authorize a live restore.

## Environment inventory

| Environment | Compose file | Database service | App service | Pre-deploy backup directory |
|---|---|---|---|---|
| Development | `docker-compose.dev.yml` | `db` | `app` | `/opt/backups/patt-db/development/` |
| Test | `docker-compose.test.yml` | `db` | `app` | `/opt/backups/patt-db/test/` |
| Production | `docker-compose.guild.yml` | `db-prod` | `app-prod` | `/opt/backups/patt-db/production/` |

All three databases are PostgreSQL 16 Compose services. The new application
container runs `alembic upgrade head` in `docker-entrypoint.sh`, so the verified
backup must complete before `docker compose up` is allowed to start that image.

## Enforced pre-deployment backup

Each deployment workflow resolves an exact target commit and records the last
successfully verified deployed commit in `.deployment/active-sha`. The state
file is updated atomically only after health, runtime identity, database, and
Alembic head checks pass; the first reconciled deployment falls back to the
server checkout's exact commit. After the new image builds, but before it can
start or migrate the database, the workflow invokes:

```text
deploy/patt-predeploy-backup.sh
```

The script resolves the database name and role from the running database
container's `POSTGRES_DB` and `POSTGRES_USER` values for deployments, then
validates both as simple PostgreSQL identifiers. This preserves compatibility
with existing volumes initialized under legacy identities without logging a
password or connection URL. Explicit `--database` and `--user` values remain
available for bounded recovery tooling.

The script fails closed unless all of these checks succeed:

1. The previous and target identities are exact 40-character commit SHAs.
2. The expected Compose file and database service exist.
3. `pg_dump` produces a non-empty PostgreSQL custom-format archive with
   ownership and ACL restoration disabled.
4. `pg_restore --list` can inspect the archive and finds the Alembic version
   object.
5. The current `patt.alembic_version` value is readable.
6. A SHA-256 digest is calculated.
7. The archive and its non-secret rollback manifest are atomically renamed from
   temporary paths.

If any step fails, the deployment stops before the migration-running container
starts. The script never restores data, downgrades a migration, moves a tag, or
changes the running application.

Archive names use this form:

```text
patt_db_YYYYMMDDTHHMMSSZ_<target-commit>.dump
patt_db_YYYYMMDDTHHMMSSZ_<target-commit>.dump.manifest
```

The manifest records the creation time, previous and target commits, database
name, Alembic revision, archive path and checksum, plus explicit markers that
restore authority is required and automatic database downgrade is disabled. It
contains no database password or connection URL.

The repository script does not delete old backups. Retention is an explicit
host-storage policy and must not remove the only usable recovery point for an
active release. Any separately installed nightly backup or retention job is
supplemental; do not treat it as verified merely because an older runbook said
it existed.

## CI recovery rehearsal

Pull-request validation uses only synthetic data and an isolated PostgreSQL
database. `scripts/rehearse_database_recovery.py`:

1. adds a deterministic recovery probe to the isolated migrated database;
2. creates and inspects a custom-format archive;
3. restores it only into a database whose name begins `patt_recovery_`;
4. compares Alembic identity, schema/table inventory, deterministic probe rows,
   and a stable SHA-256 fingerprint;
5. runs `alembic current --check-heads`;
6. downgrades exactly one representative revision, proves the head changed,
   upgrades back to head, and proves the original fingerprint and migration
   identity returned; and
7. drops the isolated restore database and probe.

CI separately starts the production image against a fresh ephemeral PostgreSQL
16 Compose service. It fails unless `/api/health` reports the expected version,
exact commit, `recovery` environment, and connected database, followed by a
successful Alembic head check. It then executes the same
`patt-predeploy-backup.sh` wrapper used on servers against that synthetic
database and retains the resulting archive and manifest.

This proves the exercised migration and synthetic archive path. It does not
prove that every historical migration is safely reversible or that a particular
live backup is usable.

## Inspecting an archive without restoring it

Inspection is read-only and does not require stopping the application:

```bash
sha256sum /opt/backups/patt-db/ENVIRONMENT/patt_db_*.dump
docker compose -f COMPOSE_FILE exec -T DB_SERVICE pg_restore --list \
  < /opt/backups/patt-db/ENVIRONMENT/patt_db_TIMESTAMP_SHA.dump
```

Compare the checksum, target SHA, database, and Alembic revision with the
adjacent manifest. A missing file, mismatched checksum, unreadable archive, or
unknown migration identity is a blocker, not a warning.

## Rollback decision boundary

Rollback is selected from the exact failure point and compatibility evidence:

| Situation | Bounded response |
|---|---|
| Failure before the new app starts | The running app/database are unchanged. Preserve the failed run and backup evidence before retrying. |
| Code-only failure with a database known to remain backward-compatible | With explicit rollback authority, deploy the exact prior immutable validated tag/SHA recorded in the manifest. Never move or reuse a tag. Re-run identity, health, DB, and Alembic checks. |
| Migration may be incompatible with prior code | Do not automatically check out old code or run `alembic downgrade`. Stop and choose a compatible code/database pair using the migration review and the verified backup. |
| Data loss, incompatible migration, or failed downgrade | A database restore is destructive live-data work. It requires Mike's exact authorization, stopped/bounded writes, a new safety backup of current state, isolated verification of the chosen archive, and an explicit compatible target tag/SHA. |

The one-revision CI downgrade is evidence only for the revision exercised in
that run. It is never blanket authorization to downgrade Production.

## Required restore plan and evidence

Before an authorized live restore, record without secrets:

- environment and incident scope;
- exact current version, tag/SHA, and Alembic revision;
- selected archive path, checksum, timestamp, database identity, and manifest;
- isolated restore/fingerprint result;
- exact compatible target version and immutable tag/SHA;
- how writes will be stopped and how a pre-restore safety backup will be kept;
- validation owner and rollback/abort criteria.

After the authorized restore, record the ending version/tag/SHA, health response,
database connectivity, Alembic head, relevant data checks, workflow/operator
result, and the location of the pre-restore safety backup. Never copy secrets,
connection strings, production rows, or user data into issues or logs.

## Data recovery judgment

Prefer rebuilding derived data when the source operation is idempotent and the
data is not user-owned. Restore is normally required for user-owned records such
as gear plans and selected slots. When uncertain, preserve current state and
stop for a recovery decision; do not experiment on a live database.
