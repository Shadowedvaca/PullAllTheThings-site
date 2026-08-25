#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?environment-scoped DEPLOY_HOST is required}"
: "${DEPLOY_USER:?environment-scoped DEPLOY_USER is required}"
: "${DEPLOY_SSH_PRIVATE_KEY_PATH:?configured private-key path is required}"
: "${DEPLOY_SSH_KNOWN_HOSTS_PATH:?configured known-hosts path is required}"

test -s "$DEPLOY_SSH_PRIVATE_KEY_PATH"
test -s "$DEPLOY_SSH_KNOWN_HOSTS_PATH"

exec ssh \
  -F /dev/null \
  -i "$DEPLOY_SSH_PRIVATE_KEY_PATH" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$DEPLOY_SSH_KNOWN_HOSTS_PATH" \
  -o LogLevel=ERROR \
  -- "$DEPLOY_USER@$DEPLOY_HOST" "$@"
