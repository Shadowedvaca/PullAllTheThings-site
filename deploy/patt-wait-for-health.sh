#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 <development|test|production> <version> <sha>" >&2
  exit 2
fi

environment="$1"
version="$2"
deployment_sha="$3"

[[ "$environment" == "development" || "$environment" == "test" || "$environment" == "production" ]]
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ "$deployment_sha" =~ ^[0-9a-f]{40}$ ]]

# Thirty attempts at three-second intervals provide a bounded 90-second window.
# Progress is intentionally non-secret and appears every 15 seconds.
for attempt in {1..30}; do
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
    exit 0
  fi
  if ((attempt % 5 == 0)); then
    printf 'Deployment readiness still pending (%ss elapsed)\n' "$((attempt * 3))" >&2
  fi
done

echo "Deployment readiness failed after 90s" >&2
exit 1
