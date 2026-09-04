#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <prepare|activate> <development|test|production> <sha> <version> <previous-sha> [tag] [test-run-id]" >&2
  exit 2
}

[[ $# -ge 5 ]] || usage

phase="$1"
environment="$2"
deployment_sha="$3"
version="$4"
previous_sha="$5"
release_tag="${6:-}"
test_run_id="${7:-}"

sha_pattern='^[0-9a-f]{40}$'
version_pattern='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
tag_pattern='^prod-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

[[ "$phase" == "prepare" || "$phase" == "activate" ]] || usage
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
prepared_record=".deployment/prepared-$deployment_sha"

if [[ "$phase" == "prepare" ]]; then
  # An orphaned preparation cannot start an application or run a migration.
  # Activation requires a new SSH connection and this verified record.
  COMMIT_SHA="$deployment_sha" docker compose -f "$compose_file" build "$app_service" </dev/null

  evidence_tmp=".deployment/backup-evidence-$deployment_sha.tmp"
  prepared_tmp="$prepared_record.tmp"
  cleanup_prepare() {
    rm -f -- "$evidence_tmp" "$prepared_tmp"
  }
  trap cleanup_prepare EXIT
  bash deploy/patt-predeploy-backup.sh \
    --compose-file "$compose_file" \
    --db-service "$db_service" \
    --database-url-service "$app_service" \
    --database-url-env DATABASE_URL \
    --backup-dir "$backup_dir" \
    --previous-sha "$previous_sha" \
    --deployment-sha "$deployment_sha" </dev/null | tee "$evidence_tmp"

  grep -Fq "Verified pre-deployment backup:" "$evidence_tmp"
  grep -Fq "Rollback manifest:" "$evidence_tmp"
  archive="$(sed -n 's/^Verified pre-deployment backup: //p' "$evidence_tmp")"
  manifest="$(sed -n 's/^Rollback manifest: //p' "$evidence_tmp")"
  [[ "$archive" =~ ^${backup_dir}/patt_db_[0-9]{8}T[0-9]{6}Z_${deployment_sha}\.dump$ ]]
  test "$manifest" = "$archive.manifest"
  test -s "$archive"
  test -s "$manifest"
  archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
  grep -Fqx "previous_sha=$previous_sha" "$manifest"
  grep -Fqx "deployment_sha=$deployment_sha" "$manifest"
  grep -Fqx "archive=$archive" "$manifest"
  grep -Fqx "archive_sha256=$archive_sha256" "$manifest"

  umask 077
  cat > "$prepared_tmp" <<EOF
schema_version=1
environment=$environment
deployment_sha=$deployment_sha
version=$version
previous_sha=$previous_sha
release_tag=$release_tag
test_run_id=$test_run_id
archive=$archive
manifest=$manifest
archive_sha256=$archive_sha256
EOF
  mv -- "$prepared_tmp" "$prepared_record"
  trap - EXIT
  rm -f -- "$evidence_tmp"
  printf 'PATT_DEPLOYMENT_PREPARED environment=%s version=%s commit=%s\n' \
    "$environment" "$version" "$deployment_sha"
  exit 0
fi

# Activation revalidates every immutable input and the backup evidence before
# the first operation that can change the running application.
test -s "$prepared_record"
grep -Fqx "schema_version=1" "$prepared_record"
grep -Fqx "environment=$environment" "$prepared_record"
grep -Fqx "deployment_sha=$deployment_sha" "$prepared_record"
grep -Fqx "version=$version" "$prepared_record"
grep -Fqx "previous_sha=$previous_sha" "$prepared_record"
grep -Fqx "release_tag=$release_tag" "$prepared_record"
grep -Fqx "test_run_id=$test_run_id" "$prepared_record"
archive="$(sed -n 's/^archive=//p' "$prepared_record")"
manifest="$(sed -n 's/^manifest=//p' "$prepared_record")"
archive_sha256="$(sed -n 's/^archive_sha256=//p' "$prepared_record")"
[[ "$archive" =~ ^${backup_dir}/patt_db_[0-9]{8}T[0-9]{6}Z_${deployment_sha}\.dump$ ]]
test "$manifest" = "$archive.manifest"
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]]
test -s "$archive"
test -s "$manifest"
test "$(sha256sum "$archive" | awk '{print $1}')" = "$archive_sha256"
grep -Fqx "previous_sha=$previous_sha" "$manifest"
grep -Fqx "deployment_sha=$deployment_sha" "$manifest"
grep -Fqx "archive=$archive" "$manifest"
grep -Fqx "archive_sha256=$archive_sha256" "$manifest"

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
rm -f .deployment/pending-previous-sha "$prepared_record"

printf 'PATT_DEPLOYMENT_COMPLETE environment=%s version=%s commit=%s\n' \
  "$environment" "$version" "$deployment_sha"
