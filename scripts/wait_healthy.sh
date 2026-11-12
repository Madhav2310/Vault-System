#!/usr/bin/env bash
# Wait until every default (non-profile) compose service is healthy. Usage: scripts/wait_healthy.sh [timeout_s]
set -euo pipefail
timeout=${1:-120}
services=$(docker compose config --services)
deadline=$(( $(date +%s) + timeout ))
while :; do
  pending=()
  for s in $services; do
    id=$(docker compose ps -q "$s" 2>/dev/null || true)
    if [ -n "$id" ]; then
      state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")
    else
      state=missing
    fi
    [ "$state" = healthy ] || pending+=("$s=$state")
  done
  if [ ${#pending[@]} -eq 0 ]; then
    echo "all healthy: $(echo $services)"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "not healthy after ${timeout}s: ${pending[*]}" >&2
    exit 1
  fi
  sleep 3
done
