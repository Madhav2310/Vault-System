# Progress log

One entry per task, newest last. A task's checkbox in `docs/plan/` is ticked only when its entry below shows evidence. Shortfalls and postponed work go to `docs/followups.md`.

## Entry template
```
### <task ID> — <title>
- Date: YYYY-MM-DD · Commit: <sha>
- Commands: <commands run>
- Output: <real output summary>
- Status: VERIFIED | UNVERIFIED (<what and why>)
- [T] changes approved: none | <metric: old → new, approved by user on date>
```

## Threshold changes
| Date | Task | Metric | Old | New | Approved by |
|---|---|---|---|---|---|

---

### S0.01 — Repository init
- Date: 2026-11-12 · Commit: 2751c85
- Commands: `git init -b main`; `git rev-parse --abbrev-ref HEAD`; `git ls-files docs/archive | wc -l`; `git ls-files | grep -c DS_Store`; `git show --stat HEAD`
- Output: branch `main`; 0 archive files tracked; 0 `.DS_Store`; first commit lists `CLAUDE.md`, `docs/design.md`, `docs/plan/*`, `docs/research/*`, `docs/spec/case-study.pdf` (43 files).
- Status: VERIFIED
- [T] changes approved: none

### S0.02 — Python project and tooling
- Date: 2026-11-12 · Commit: d1219bb
- Commands: `cd backend && uv sync --frozen`; `make lint`; `make typecheck`; `make -n <each target>`; `make ci`
- Output: sync exit 0 (CPython 3.12.12, uv-managed, 39 packages); lint exit 0; mypy strict "no issues found in 2 source files"; all 17 targets defined (unimplemented ones print the owning task and exit 1); `make ci` exit 0, `tests/unit/test_smoke.py::test_import_app` passed.
- Status: VERIFIED
- Incident: the first `uv lock` probed Homebrew `python@3.13` during interpreter discovery and macOS killed it (SIGKILL, Code Signature Invalid, while mapping `lib-dynload/binascii.cpython-313-darwin.so`; reports `~/Library/Logs/DiagnosticReports/Python-2026-11-12-2249*.ips`, `-225045.ips`). Fix: `[tool.uv] python-preference = "only-managed"` + `backend/.python-version` = 3.12, so uv never launches Homebrew/system interpreters. No crash since. See followups F-001.
- [T] changes approved: none

### S0.03 — Progress and AI-usage logs
- Date: 2026-11-12 · Commit: (this commit)
- Commands: S0.03 AC1 and AC2 grep commands (threshold-table heading count; AI_USAGE `## ` section count)
- Output: 1; 5
- Status: VERIFIED
- Also: `scripts/plan_lint.py` and `scripts/plan_renumber.py` now accept/preserve ticked `[x]` boxes (they assumed `[ ]` only).
- [T] changes approved: none

### S0.05 — Settings
- Date: 2026-11-12 · Commit: 0dc4535
- Commands: `make test-unit` (tests/unit/core/test_settings.py)
- Output: 4 passed — missing DATABASE_URL raises naming `database_url`; every field in `.env.example`; API keys are `SecretStr` and masked in repr/dump; model defaults `claude-sonnet-5-5` / `claude-haiku-5-5` / `claude-opus-5-5`, no date suffix.
- Status: VERIFIED
- [T] changes approved: none

### S0.14 — Celery application
- Date: 2026-11-12 · Commit: 8efa712 (code) · e2e verified after compose up
- Commands: `make test-unit` (test_celery_config.py); `make test-e2e` (test_celery_ping.py)
- Output: reliability settings asserted (acks_late, reject_on_worker_lost, prefetch 1, visibility 3600 s); every `vault.*` task routed; `ping` round-trips through `ingest`, `ai`, `maintenance` (3 passed against compose).
- Status: VERIFIED
- Note: added `celery-types` (dev) so `app/` stays mypy-strict.
- [T] changes approved: none

### S0.06 — Structured logging
- Date: 2026-11-12 · Commit: 9df3791 (shared with S0.07: the app wiring and test-app fixture are common)
- Commands: `make test-unit` (test_logging.py); `make test-integration` (tests/integration/api/test_logging_context.py)
- Output: JSON lines for structlog and stdlib (uvicorn) records; access line carries `request_id` (= response header) and a 32-hex `trace_id`; valid incoming `X-Request-ID` echoed, malformed one replaced; 20 concurrent requests keep their own `request_id`.
- Status: VERIFIED, with one caveat: the trace_id in AC2 comes from an ambient span the test starts (FastAPI server spans arrive with S0.15). S0.15 AC1 re-checks this with real instrumentation.
- [T] changes approved: none

### S0.07 — Error model (problem+json)
- Date: 2026-11-12 · Commit: 9df3791
- Commands: `make test-integration` (test_errors.py); `make test-unit` (test_error_codes.py)
- Output: 404 `not_found` problem+json; 422 `validation_error` that never echoes submitted values; 500 `internal_error` with `detail: null` and no traceback/paths; all 20 `ErrorCode`s have status + retryable.
- Status: VERIFIED
- [T] changes approved: none

### S0.11 — Object store (local)
- Date: 2026-11-12 · Commit: ad75d43
- Commands: `make test-unit` (tests/unit/storage/test_local.py)
- Output: 12 passed — round-trip; identical put keeps mtime; simulated crash before rename leaves the old object and no temp file; 9 traversal/absolute/odd keys rejected.
- Status: VERIFIED
- [T] changes approved: none

### S0.08 — Database models and initial migration
- Date: 2026-11-12 · Commit: 52a8df2
- Commands: `make migrate` (dev DB, inside the api image); `make test-integration` (tests/integration/db/*); `make test-unit` (test_ids.py)
- Output: dev DB at revision `0001`, `chunks` has 32 partitions; 31 integration tests passed: down→up round-trip, all 26 §11 tables, `HASH (owner_id)` ×32, `halfvec(1536)`, 16 unique/PK constraints from §11, `NULLS NOT DISTINCT` on feedback, `vector` 0.8.7 + `btree_gin`, `tsv_en` STORED generated, composite GIN `(owner_id, tsv_en)` and `(owner_id, tsv_ids)`, **ORM↔DB drift test** (columns/types/nullability). UUIDv7 monotonic across 20k IDs.
- Status: VERIFIED
- Beyond §11 (stated reasons): `users.api_key_hash UNIQUE` (auth lookup); `UNIQUE(run_id, seq)` on run_steps and `UNIQUE(run_id, ordinal)` on answer_segments (idempotent ledger writes); `CHECK sentence_start < sentence_end` (half-open ranges); `created_at` on every table (per §11 preamble); extra indexes on `idempotency_keys(created_at)` and `job_events(created_at)` (TTL purges).
- [T] changes approved: none

### S0.04 — docker-compose with pinned images
- Date: 2026-11-12 · Files landed in 9df3791 and 52a8df2 (bundled by `git add -A`); verified in this commit
- Commands: `docker compose config -q`; `make test-unit` (test_compose.py); `docker compose down && docker compose up -d && scripts/wait_healthy.sh 120`; pgvector version query; `docker compose ps beat --format json | jq -s length`
- Output: config valid; all images pinned (pgvector 0.8.7-pg16-bookworm, redis 7.4.9-alpine, otel-lgtm 0.38.1, phoenix version-21.0.0, postgres-exporter v0.20.1, pgbouncer v1.26.1-p0, seaweedfs 4.50, local builds 0.1.0); **all 8 default services healthy 37 s after a cold `up`**; `vector` 0.8.7; beat = 1 replica with `deploy.replicas: 1`.
- Status: VERIFIED
- Plan edits: AC2's grep flagged every valid tag that ends in a letter (`-alpine`, `-bookworm`), so it was replaced by `t:unit/ops/test_compose.py::test_images_pinned`; AC5 `jq length` → `jq -s length` (compose prints one JSON object per line).
- Host ports: api 8100, postgres 5442, redis 6389, reranker 8180, grafana 3000, OTLP 4317/4318, all on 127.0.0.1 and overridable. Port 8000 is taken by another local project's container (left untouched).
- Also: a minimal `/healthz` was added for the api healthcheck (S0.16 adds `/readyz`).
- [T] changes approved: none

### S0.09 — API-key auth and seeded dev users
- Date: 2026-11-12 · Commit: cc0bb36
- Commands: `make test-integration` (test_auth.py, test_users.py, test_seed.py); `make seed-users` ×2 against compose
- Output: missing / wrong / malformed key → 401 `unauthenticated` with `WWW-Authenticate: Bearer`; valid key resolves alice (admin) and bob; only SHA-256 hashes stored; second seed creates no rows; live seed printed 2 keys once (redacted in the transcript), second run "exists (key unchanged)".
- Status: VERIFIED
- [T] changes approved: none

### S0.10 — Owner-scoped repository pattern
- Date: 2026-11-12 · Commit: c417f17
- Commands: `make test-unit` (tests/unit/db/test_repository_contract.py)
- Output: 2 passed — every Repository subclass in `app/` on an owned model extends OwnedRepository and every public method takes `owner_id` first; `_scoped()` always compiles to `documents.owner_id = '<uuid>'`; a missing owner raises.
- Status: VERIFIED
- [T] changes approved: none

### S0.16 — Health endpoints
- Date: 2026-11-12 · Commit: (this commit)
- Commands: `make test-integration` (tests/integration/api/test_health.py) with the compose stack up
- Output: 4 passed — `/healthz` 200 with dead DB/Redis URLs; `/readyz` 200 with all 5 checks ok; Redis on a closed port → 503 with only `redis` failing; DB down → 503 with only `database` failing and no password in the body.
- Status: VERIFIED
- [T] changes approved: none
