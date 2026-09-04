#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: patt-predeploy-backup.sh \
  --compose-file PATH --db-service NAME \
  (--database NAME --user NAME | --database-env NAME --user-env NAME | \
   --database-url-service NAME --database-url-env NAME) \
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
database_env=""
user_env=""
database_url_service=""
database_url_env=""
backup_dir=""
previous_sha=""
deployment_sha=""

while (($#)); do
  case "$1" in
    --compose-file) compose_file="${2:-}"; shift 2 ;;
    --db-service) db_service="${2:-}"; shift 2 ;;
    --database) database_name="${2:-}"; shift 2 ;;
    --user) database_user="${2:-}"; shift 2 ;;
    --database-env) database_env="${2:-}"; shift 2 ;;
    --user-env) user_env="${2:-}"; shift 2 ;;
    --database-url-service) database_url_service="${2:-}"; shift 2 ;;
    --database-url-env) database_url_env="${2:-}"; shift 2 ;;
    --backup-dir) backup_dir="${2:-}"; shift 2 ;;
    --previous-sha) previous_sha="${2:-}"; shift 2 ;;
    --deployment-sha) deployment_sha="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for value in compose_file db_service backup_dir previous_sha deployment_sha; do
  if [[ -z "${!value}" ]]; then
    echo "Missing required value: $value" >&2
    exit 2
  fi
done

if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]] || [[ ! "$deployment_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Previous and deployment identities must be exact 40-character commits" >&2
  exit 2
fi
direct_identity=0
container_env_identity=0
database_url_identity=0
if [[ -n "$database_name" || -n "$database_user" ]]; then
  [[ -n "$database_name" && -n "$database_user" ]] || { echo "Direct database identity requires both database and user" >&2; exit 2; }
  direct_identity=1
fi
if [[ -n "$database_env" || -n "$user_env" ]]; then
  [[ -n "$database_env" && -n "$user_env" ]] || { echo "Container environment identity requires both database and user variables" >&2; exit 2; }
  container_env_identity=1
fi
if [[ -n "$database_url_service" || -n "$database_url_env" ]]; then
  [[ -n "$database_url_service" && -n "$database_url_env" ]] || { echo "Database URL identity requires both service and variable names" >&2; exit 2; }
  database_url_identity=1
fi
if ((direct_identity + container_env_identity + database_url_identity != 1)); then
  echo "Provide exactly one complete database identity source" >&2
  exit 2
fi
if [[ -n "$database_env" ]]; then
  [[ "$database_env" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "Invalid database environment variable name" >&2; exit 2; }
  database_name="$(docker compose -f "$compose_file" exec -T "$db_service" printenv "$database_env")"
fi
if [[ -n "$user_env" ]]; then
  [[ "$user_env" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "Invalid user environment variable name" >&2; exit 2; }
  database_user="$(docker compose -f "$compose_file" exec -T "$db_service" printenv "$user_env")"
fi
if [[ -n "$database_url_service" ]]; then
  [[ "$database_url_service" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "Invalid database URL service name" >&2; exit 2; }
  [[ "$database_url_env" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "Invalid database URL environment variable name" >&2; exit 2; }
  database_identity="$({
    docker compose -f "$compose_file" config --format json |
      python3 -c '
import json
import re
import sys
from urllib.parse import unquote, urlparse

service_name, variable_name = sys.argv[1:3]
configuration = json.load(sys.stdin)
try:
    value = configuration["services"][service_name]["environment"][variable_name]
except (KeyError, TypeError):
    raise SystemExit("Configured application database URL was not found")
parsed = urlparse(value)
database_user = unquote(parsed.username or "")
database_name = unquote(parsed.path.lstrip("/"))
identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
    raise SystemExit("Configured application database URL is not PostgreSQL")
if not identifier.fullmatch(database_user) or not identifier.fullmatch(database_name):
    raise SystemExit("Configured application database identity is invalid")
print(database_user)
print(database_name)
' "$database_url_service" "$database_url_env"
  })" || exit 1
  mapfile -t database_identity_lines <<<"$database_identity"
  [[ "${#database_identity_lines[@]}" -eq 2 ]] || { echo "Configured application database identity is incomplete" >&2; exit 1; }
  database_user="${database_identity_lines[0]}"
  database_name="${database_identity_lines[1]}"
  unset database_identity database_identity_lines
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
  if [[ -n "${dump_pid:-}" ]] && kill -0 "$dump_pid" 2>/dev/null; then
    kill "$dump_pid" 2>/dev/null || true
    wait "$dump_pid" 2>/dev/null || true
  fi
  rm -f -- "$archive_tmp" "$manifest_tmp" "$listing_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

heartbeat_seconds="${PATT_BACKUP_HEARTBEAT_SECONDS:-15}"
[[ "$heartbeat_seconds" =~ ^[1-9][0-9]*$ ]] || {
  echo "PATT_BACKUP_HEARTBEAT_SECONDS must be a positive integer" >&2
  exit 2
}
started_at="$SECONDS"
next_heartbeat="$((SECONDS + heartbeat_seconds))"
docker compose -f "$compose_file" exec -T "$db_service" \
  pg_dump --username "$database_user" --dbname "$database_name" \
  --format=custom --no-owner --no-acl > "$archive_tmp" &
dump_pid=$!
while kill -0 "$dump_pid" 2>/dev/null; do
  sleep 1
  if kill -0 "$dump_pid" 2>/dev/null && ((SECONDS >= next_heartbeat)); then
    printf 'Pre-deployment backup still running (%ss elapsed)\n' \
      "$((SECONDS - started_at))" >&2
    next_heartbeat="$((SECONDS + heartbeat_seconds))"
  fi
done
if ! wait "$dump_pid"; then
  echo "Pre-deployment pg_dump failed" >&2
  exit 1
fi
dump_pid=""
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
