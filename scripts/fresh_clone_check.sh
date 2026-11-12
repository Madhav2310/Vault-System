#!/usr/bin/env bash
# S0.23 AC1: clone HEAD into a temp dir, bring the stack up under a separate compose project, wait healthy.
# The dev stack must be stopped first (shared host ports):  make down
set -euo pipefail
src=$(git rev-parse --show-toplevel)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/vault-fresh.XXXXXX")
trap 'COMPOSE_PROJECT_NAME=vaultfresh docker compose -f "$tmp/repo/docker-compose.yml" --project-directory "$tmp/repo" down -v >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
git clone -q "$src" "$tmp/repo"
cd "$tmp/repo"
export COMPOSE_PROJECT_NAME=vaultfresh
docker compose up -d --build
scripts/wait_healthy.sh 180
echo "fresh clone healthy ($(git rev-parse --short HEAD))"
