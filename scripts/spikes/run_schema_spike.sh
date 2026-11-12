#!/usr/bin/env bash
# Usage: scripts/spikes/run_schema_spike.sh [ddl|explain]  — runs against scratch DB vault_spike in compose postgres.
set -euo pipefail
cd "$(dirname "$0")/../.."
psql_() { docker compose exec -T postgres psql -U vault -X -q "$@"; }
if [ "${1:-ddl}" = ddl ]; then
  psql_ -d postgres -c "DROP DATABASE IF EXISTS vault_spike WITH (FORCE)" -c "CREATE DATABASE vault_spike"
  psql_ -d vault_spike < scripts/spikes/schema_spike.sql
else
  psql_ -d vault_spike < scripts/spikes/schema_spike_explain.sql
fi
