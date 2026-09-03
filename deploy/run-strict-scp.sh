#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?environment-scoped DEPLOY_HOST is required}"
: "${DEPLOY_USER:?environment-scoped DEPLOY_USER is required}"
: "${DEPLOY_SSH_PRIVATE_KEY_PATH:?configured private-key path is required}"
: "${DEPLOY_SSH_KNOWN_HOSTS_PATH:?configured known-hosts path is required}"

test -s "$DEPLOY_SSH_PRIVATE_KEY_PATH"
test -s "$DEPLOY_SSH_KNOWN_HOSTS_PATH"

if (( $# != 2 )); then
  echo "usage: $0 LOCAL_FILE REMOTE_BUNDLE_PATH" >&2
  exit 2
fi
test -f "$1"
printf '%s\n' "$2" | grep -Eq '^/tmp/patt-deployment-[0-9a-f]{40}\.bundle$'

exec scp \
  -F /dev/null \
  -i "$DEPLOY_SSH_PRIVATE_KEY_PATH" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$DEPLOY_SSH_KNOWN_HOSTS_PATH" \
  -o LogLevel=ERROR \
  -- "$1" "$DEPLOY_USER@$DEPLOY_HOST:$2"
