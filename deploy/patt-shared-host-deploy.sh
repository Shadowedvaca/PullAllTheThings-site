#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <prepare|activate> <development|test> <sha> <version>" >&2
  exit 2
}

[[ $# -eq 4 ]] || usage
phase="$1"
environment="$2"
deployment_sha="$3"
version="$4"

[[ "$phase" == "prepare" || "$phase" == "activate" ]] || usage
[[ "$environment" == "development" || "$environment" == "test" ]] || usage
[[ "$deployment_sha" =~ ^[0-9a-f]{40}$ ]] || usage
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage

project="patt"
lock_path="/run/lock/shared-platform-deployment.lock"
lock_wait_seconds=2700
minimum_root_kib=$((12 * 1024 * 1024))
minimum_swap_kib=$((1 * 1024 * 1024))
minimum_headroom_kib=$((2 * 1024 * 1024))

command -v flock >/dev/null
printf 'SHARED_DEPLOYMENT_LOCK_WAIT project=%s environment=%s phase=%s commit=%s timeout_seconds=%s\n' \
  "$project" "$environment" "$phase" "$deployment_sha" "$lock_wait_seconds"
exec 9>"$lock_path"
if ! flock -w "$lock_wait_seconds" 9; then
  printf 'SHARED_DEPLOYMENT_LOCK_TIMEOUT project=%s environment=%s phase=%s commit=%s\n' \
    "$project" "$environment" "$phase" "$deployment_sha" >&2
  exit 75
fi

cleanup_bundle=""
cleanup_wrapper=""
release_lock() {
  local status=$?
  local result="failure"
  if [[ -n "$cleanup_bundle" ]]; then
    rm -f -- "$cleanup_bundle"
  fi
  if [[ -n "$cleanup_wrapper" ]]; then
    rm -f -- "$cleanup_wrapper"
  fi
  if ((status == 0)); then
    result="success"
  fi
  printf 'SHARED_DEPLOYMENT_LOCK_RELEASE project=%s environment=%s phase=%s commit=%s result=%s status=%s\n' \
    "$project" "$environment" "$phase" "$deployment_sha" "$result" "$status"
  return "$status"
}
trap release_lock EXIT

printf 'SHARED_DEPLOYMENT_LOCK_ACQUIRED project=%s environment=%s phase=%s commit=%s\n' \
  "$project" "$environment" "$phase" "$deployment_sha"

root_available_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
swap_total_kib="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"
swap_free_kib="$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)"
memory_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
for value in "$root_available_kib" "$swap_total_kib" "$swap_free_kib" "$memory_available_kib"; do
  [[ "$value" =~ ^[0-9]+$ ]]
done
headroom_kib=$((memory_available_kib + swap_free_kib))
((root_available_kib >= minimum_root_kib))
((swap_total_kib >= minimum_swap_kib))
((headroom_kib >= minimum_headroom_kib))
printf 'SHARED_DEPLOYMENT_ADMITTED project=%s environment=%s phase=%s commit=%s root_available_kib=%s swap_total_kib=%s headroom_kib=%s\n' \
  "$project" "$environment" "$phase" "$deployment_sha" \
  "$root_available_kib" "$swap_total_kib" "$headroom_kib"

cd /opt/guild-portal
mkdir -p .deployment

if [[ "$phase" == "prepare" ]]; then
  bundle="/tmp/patt-deployment-$deployment_sha.bundle"
  wrapper="/tmp/patt-shared-host-deploy-$deployment_sha.sh"
  cleanup_bundle="$bundle"
  cleanup_wrapper="$wrapper"
  previous_sha="$(if [[ -s .deployment/active-sha ]]; then cat .deployment/active-sha; elif [[ -s .deployment/pending-previous-sha ]]; then cat .deployment/pending-previous-sha; else git rev-parse HEAD; fi)"
  [[ "$previous_sha" =~ ^[0-9a-f]{40}$ ]]
  if [[ ! -s .deployment/active-sha && ! -s .deployment/pending-previous-sha ]]; then
    printf '%s\n' "$previous_sha" > .deployment/pending-previous-sha.tmp
    mv .deployment/pending-previous-sha.tmp .deployment/pending-previous-sha
  fi
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_SYSTEM=/dev/null
  export GIT_TERMINAL_PROMPT=0
  git fetch --no-tags "$bundle" HEAD
  rm -f -- "$bundle"
  git cat-file -e "$deployment_sha^{commit}"
  git checkout --detach "$deployment_sha"
  test "$(git rev-parse HEAD)" = "$deployment_sha"
  bash deploy/patt-remote-deploy.sh prepare \
    "$environment" "$deployment_sha" "$version" "$previous_sha"
  exit 0
fi

test "$(git rev-parse HEAD)" = "$deployment_sha"
previous_sha="$(if [[ -s .deployment/active-sha ]]; then cat .deployment/active-sha; else cat .deployment/pending-previous-sha; fi)"
[[ "$previous_sha" =~ ^[0-9a-f]{40}$ ]]
bash deploy/patt-remote-deploy.sh activate \
  "$environment" "$deployment_sha" "$version" "$previous_sha"
