#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: patt-predeploy-backup.sh \
  --compose-file PATH --db-service NAME --database NAME --user NAME \
  --backup-dir PATH --previous-sha SHA --deployment-sha SHA

Creates an atomic PostgreSQL custom-format backup, verifies that pg_restore can
inspect it, and writes a non-secret rollback manifest. It never restores or
downgrades a database.
EOF
}

compose_file=""
db_service=""
database_name=""
database_user=""
backup_dir=""
previous_sha=""
deployment_sha=""

while (($#)); do
  case "$1" in
    --compose-file) compose_file="${2:-}"; shift 2 ;;
    --db-service) db_service="${2:-}"; shift 2 ;;
    --database) database_name="${2:-}"; shift 2 ;;
    --user) database_user="${2:-}"; shift 2 ;;
    --backup-dir) backup_dir="${2:-}"; shift 2 ;;
    --previous-sha) previous_sha="${2:-}"; shift 2 ;;
    --deployment-sha) deployment_sha="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for value in compose_file db_service database_name database_user backup_dir previous_sha deployment_sha; do
  if [[ -z "${!value}" ]]; then
    echo "Missing required value: $value" >&2
    exit 2
  fi
done

if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]] || [[ ! "$deployment_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Previous and deployment identities must be exact 40-character commits" >&2
  exit 2
fi
if [[ ! "$database_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || [[ ! "$database_user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Database and user names must be simple PostgreSQL identifiers" >&2
  exit 2
fi
if [[ ! -f "$compose_file" ]]; then
  echo "Compose file does not exist: $compose_file" >&2
  exit 2
fi

umask 077
mkdir -p "$backup_dir"
backup_dir="$(cd "$backup_dir" && pwd -P)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/patt_db_${timestamp}_${deployment_sha}.dump"
manifest="$archive.manifest"
archive_tmp="$archive.tmp"
manifest_tmp="$manifest.tmp"
listing_tmp="$archive.list.tmp"

if [[ -e "$archive" ]] || [[ -e "$manifest" ]]; then
  echo "Refusing to overwrite existing backup evidence" >&2
  exit 1
fi

cleanup() {
  rm -f -- "$archive_tmp" "$manifest_tmp" "$listing_tmp"
}
trap cleanup EXIT

docker compose -f "$compose_file" exec -T "$db_service" \
  pg_dump --username "$database_user" --dbname "$database_name" \
  --format=custom --no-owner --no-acl > "$archive_tmp"
test -s "$archive_tmp"

docker compose -f "$compose_file" exec -T "$db_service" \
  pg_restore --list < "$archive_tmp" > "$listing_tmp"
grep -q "alembic_version" "$listing_tmp"

archive_sha256="$(sha256sum "$archive_tmp" | awk '{print $1}')"
alembic_revision="$(
  docker compose -f "$compose_file" exec -T "$db_service" \
    psql --username "$database_user" --dbname "$database_name" \
    --no-align --tuples-only --command \
    "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM patt.alembic_version;"
)"
alembic_revision="${alembic_revision//$'\r'/}"
alembic_revision="${alembic_revision//$'\n'/}"
test -n "$alembic_revision"

mv -- "$archive_tmp" "$archive"
cat > "$manifest_tmp" <<EOF
schema_version=1
created_at_utc=$timestamp
previous_sha=$previous_sha
deployment_sha=$deployment_sha
database=$database_name
alembic_revision=$alembic_revision
archive=$archive
archive_sha256=$archive_sha256
restore_authority=explicit_required
automatic_database_downgrade=false
EOF
mv -- "$manifest_tmp" "$manifest"

echo "Verified pre-deployment backup: $archive"
echo "Rollback manifest: $manifest"
