#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <development|test|production> <sha> <version> <previous-sha> [tag] [test-run-id]" >&2
  exit 2
}

[[ $# -ge 4 ]] || usage

environment="$1"
deployment_sha="$2"
version="$3"
previous_sha="$4"
release_tag="${5:-}"
test_run_id="${6:-}"

sha_pattern='^[0-9a-f]{40}$'
version_pattern='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
tag_pattern='^prod-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

[[ "$deployment_sha" =~ $sha_pattern ]]
[[ "$previous_sha" =~ $sha_pattern ]]
[[ "$version" =~ $version_pattern ]]
test "$(git rev-parse HEAD)" = "$deployment_sha"
test "$(cat VERSION)" = "$version"

case "$environment" in
  development)
    compose_file="docker-compose.dev.yml"
    app_service="app"
    db_service="db"
    backup_dir="/opt/backups/patt-db/development"
    ;;
  test)
    compose_file="docker-compose.test.yml"
    app_service="app"
    db_service="db"
    backup_dir="/opt/backups/patt-db/test"
    ;;
  production)
    compose_file="docker-compose.guild.yml"
    app_service="app-prod"
    db_service="db-prod"
    backup_dir="/opt/backups/patt-db/production"
    [[ "$release_tag" =~ $tag_pattern ]]
    test "$release_tag" = "prod-v$version"
    [[ "$test_run_id" =~ ^[1-9][0-9]*$ ]]
    ;;
  *)
    usage
    ;;
esac

mkdir -p .deployment

# The deployment program is a checked-in file, not a script streamed on stdin.
# Explicitly detach child-process stdin as defense in depth against tools that
# opportunistically read it (Docker Buildx did so in the former SSH heredoc).
COMMIT_SHA="$deployment_sha" docker compose -f "$compose_file" build "$app_service" </dev/null

backup_evidence="$(
  bash deploy/patt-predeploy-backup.sh \
    --compose-file "$compose_file" \
    --db-service "$db_service" \
    --database-env POSTGRES_DB \
    --user-env POSTGRES_USER \
    --backup-dir "$backup_dir" \
    --previous-sha "$previous_sha" \
    --deployment-sha "$deployment_sha" </dev/null
)"
printf '%s\n' "$backup_evidence"
grep -Fq "Verified pre-deployment backup:" <<<"$backup_evidence"
grep -Fq "Rollback manifest:" <<<"$backup_evidence"

COMMIT_SHA="$deployment_sha" docker compose -f "$compose_file" up -d "$app_service" </dev/null

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  sleep 3
  if health="$(curl -sf http://localhost:8100/api/health)" && \
    printf '%s' "$health" | python3 -c '
import json
import sys

environment, version, commit = sys.argv[1:4]
data = json.load(sys.stdin)
assert data["ok"] is True
assert data["data"]["db"] == "connected"
assert data["data"]["environment"] == environment
assert data["data"]["version"] == version
assert data["data"]["commit"] == commit
' "$environment" "$version" "$deployment_sha"; then
    break
  fi
  if [[ "$attempt" -eq 10 ]]; then
    docker compose -f "$compose_file" logs "$app_service" --tail 50
    exit 1
  fi
done

COMMIT_SHA="$deployment_sha" docker compose -f "$compose_file" exec -T "$app_service" alembic current --check-heads </dev/null
printf '%s\n' "$deployment_sha" > .deployment/active-sha.tmp
mv .deployment/active-sha.tmp .deployment/active-sha
test "$(cat .deployment/active-sha)" = "$deployment_sha"

printf 'PATT_DEPLOYMENT_COMPLETE environment=%s version=%s commit=%s\n' \
  "$environment" "$version" "$deployment_sha"
