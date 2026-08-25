#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${DEPLOY_SSH_PRIVATE_KEY:?environment-scoped DEPLOY_SSH_KEY is required}"
: "${DEPLOY_SSH_KNOWN_HOSTS:?environment-scoped DEPLOY_KNOWN_HOSTS is required}"

ssh_dir="$RUNNER_TEMP/patt-deployment-ssh"
private_key="$ssh_dir/id_deploy"
known_hosts="$ssh_dir/known_hosts"

umask 077
mkdir -p -- "$ssh_dir"
printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" > "$private_key"
printf '%s\n' "$DEPLOY_SSH_KNOWN_HOSTS" > "$known_hosts"
chmod 600 -- "$private_key" "$known_hosts"

# Validate both inputs without printing key or host material. Deployment keys must
# be non-interactive, and known-host data must already be obtained through a
# separately trusted channel; this script deliberately never scans the host.
ssh-keygen -y -P '' -f "$private_key" >/dev/null
ssh-keygen -l -f "$known_hosts" >/dev/null

{
  printf 'private_key=%s\n' "$private_key"
  printf 'known_hosts=%s\n' "$known_hosts"
} >> "$GITHUB_OUTPUT"
