#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 COMPOSE_FILE DB_SERVICE BACKUP_DIR PREVIOUS_SHA DEPLOYMENT_SHA" >&2
  exit 2
fi

compose_file="$1"
db_service="$2"
backup_dir="$3"
previous_sha="$4"
deployment_sha="$5"
failed_dir="$backup_dir/hard-coded-identity"
corrected_dir="$backup_dir/container-derived-identity"

mkdir -p "$failed_dir" "$corrected_dir"

# Reproduce the original Production defect against real isolated PostgreSQL:
# this legacy-initialized container has neither guild_user nor guild_db.
resolved_database="$(
  docker compose -f "$compose_file" exec -T "$db_service" printenv POSTGRES_DB
)"
resolved_user="$(
  docker compose -f "$compose_file" exec -T "$db_service" printenv POSTGRES_USER
)"
test "$resolved_database" = "patt_recovery"
test "$resolved_user" = "patt_recovery"
missing_role="$(
  docker compose -f "$compose_file" exec -T "$db_service" \
    psql --username "$resolved_user" --dbname "$resolved_database" \
    --no-align --tuples-only --command \
    "SELECT count(*) FROM pg_roles WHERE rolname = 'guild_user';"
)"
missing_database="$(
  docker compose -f "$compose_file" exec -T "$db_service" \
    psql --username "$resolved_user" --dbname "$resolved_database" \
    --no-align --tuples-only --command \
    "SELECT count(*) FROM pg_database WHERE datname = 'guild_db';"
)"
test "${missing_role//$'\r'/}" = "0"
test "${missing_database//$'\r'/}" = "0"

set +e
bash deploy/patt-predeploy-backup.sh \
  --compose-file "$compose_file" \
  --db-service "$db_service" \
  --database guild_db \
  --user guild_user \
  --backup-dir "$failed_dir" \
  --previous-sha "$previous_sha" \
  --deployment-sha "$deployment_sha" </dev/null
hard_coded_status=$?
set -e

if [[ "$hard_coded_status" -eq 0 ]]; then
  echo "Hard-coded database identity unexpectedly succeeded" >&2
  exit 1
fi
if find "$failed_dir" -type f -print -quit | grep -q .; then
  echo "Failed hard-coded attempt retained partial backup evidence" >&2
  exit 1
fi

bash deploy/patt-predeploy-backup.sh \
  --compose-file "$compose_file" \
  --db-service "$db_service" \
  --database-env POSTGRES_DB \
  --user-env POSTGRES_USER \
  --backup-dir "$corrected_dir" \
  --previous-sha "$previous_sha" \
  --deployment-sha "$deployment_sha" </dev/null

archive="$(find "$corrected_dir" -maxdepth 1 -type f -name '*.dump' -print -quit)"
manifest="$archive.manifest"
test -n "$archive"
test -s "$archive"
test -s "$manifest"
grep -Fqx "database=patt_recovery" "$manifest"
grep -Fqx "alembic_revision=0182" "$manifest"
grep -Fqx "restore_authority=explicit_required" "$manifest"
grep -Fqx "automatic_database_downgrade=false" "$manifest"

echo "Legacy database identity regression passed: hard-coded identity failed; container-derived identity produced verified evidence"
