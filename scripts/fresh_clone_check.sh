#!/usr/bin/env bash
# Reviewer-path check: clone the repo into a temp dir and run what a reviewer would run there.
#
#   scripts/fresh_clone_check.sh [--no-stack|--stack] [SOURCE]
#
# SOURCE is a path or URL (default: this checkout; only committed files are cloned).
# --no-stack (default): prerequisites, .env from .env.example, `make ci` (no Docker).
# --stack: also `make up`, wait for /readyz, the core demo smoke (`scripts/demo_core.sh`), `make down`.
#   Needs Docker running, free ports 8100/5442/6389 (run `make down` first) and OPENAI_API_KEY in the
#   environment, which is written to the clone's .env (never printed). Without it the smoke step is skipped.
# Prints a pass/fail table; exits non-zero when any step fails. The temp dir is removed on exit.
set -uo pipefail

stack=0
src=""
for arg in "$@"; do
  case "$arg" in
    --stack) stack=1 ;;
    --no-stack) stack=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) src="$arg" ;;
  esac
done
src="${src:-$(cd "$(dirname "$0")/.." && pwd)}"

names=(); results=(); failed=0
record() { names+=("$1"); results+=("$2"); [ "$2" = FAIL ] && failed=1; return 0; }
run_step() { # name, command...
  local name="$1"; shift
  if "$@"; then record "$name" PASS; else record "$name" FAIL; return 1; fi
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/vault-fresh.XXXXXX")
repo="$tmp/repo"
stack_up=0
cleanup() {
  if [ "$stack_up" = 1 ] && [ -d "$repo" ]; then (cd "$repo" && make down >/dev/null 2>&1) || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

summary() {
  printf '\n%-34s %s\n' STEP RESULT
  printf '%-34s %s\n' ---------------------------------- ------
  for i in "${!names[@]}"; do printf '%-34s %s\n' "${names[$i]}" "${results[$i]}"; done
  if [ "$failed" = 0 ]; then echo "fresh clone check: PASS"; else echo "fresh clone check: FAIL"; fi
  exit "$failed"
}

prereqs() {
  local bad=0
  command -v git >/dev/null || { echo "missing: git" >&2; bad=1; }
  command -v make >/dev/null || { echo "missing: make" >&2; bad=1; }
  command -v uv >/dev/null || { echo "missing: uv (https://docs.astral.sh/uv/)" >&2; bad=1; }
  if [ "$stack" = 1 ]; then
    command -v docker >/dev/null || { echo "missing: docker" >&2; bad=1; }
    docker info >/dev/null 2>&1 || { echo "docker is not running" >&2; bad=1; }
    command -v curl >/dev/null || { echo "missing: curl" >&2; bad=1; }
  fi
  return "$bad"
}

wait_ready() {
  local url="http://localhost:${API_PORT:-8100}/readyz" i
  for i in $(seq 1 60); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 3
  done
  echo "readyz did not answer 200 within 180 s: $url" >&2
  return 1
}

smoke() {
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "OPENAI_API_KEY not set in the environment: smoke skipped" >&2
    return 2
  fi
  (cd "$repo" && scripts/demo_core.sh)
}

ci_in_clone() { (cd "$repo" && make ci); }

run_step "prerequisites" prereqs || summary
run_step "git clone" git clone -q "$src" "$repo" || summary
run_step ".env from .env.example" cp "$repo/.env.example" "$repo/.env" || summary
if [ "$stack" = 1 ] && [ -n "${OPENAI_API_KEY:-}" ]; then
  # edit the copy only; the value is never echoed
  perl -pi -e 's/^OPENAI_API_KEY=.*/"OPENAI_API_KEY=$ENV{OPENAI_API_KEY}"/e' "$repo/.env"
fi
run_step "make ci (no Docker)" ci_in_clone || true

if [ "$stack" = 1 ]; then
  stack_up=1
  if (cd "$repo" && make up); then
    record "make up" PASS
    run_step "GET /readyz" wait_ready || true
    smoke; rc=$?
    case "$rc" in 0) record "demo_core smoke" PASS ;; 2) record "demo_core smoke" SKIP ;; *) record "demo_core smoke" FAIL ;; esac
  else
    record "make up" FAIL
  fi
  if (cd "$repo" && make down >/dev/null 2>&1); then stack_up=0; record "make down" PASS; else record "make down" FAIL; fi
fi
summary
