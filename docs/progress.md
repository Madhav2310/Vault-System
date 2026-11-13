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

### S0.20 — Spike: partitioned halfvec with composite GIN
- Date: 2026-11-12 · Commit: (this commit) · Evidence: `docs/evidence/spike_schema.md`
- Commands: `scripts/spikes/run_schema_spike.sh ddl` (16.8 s, exit 0); `scripts/spikes/run_schema_spike.sh explain`; manual plans on a 21k-chunk owner
- Output: AC1 DDL ok (GIN on all 32 partitions); AC2 vector query prunes to 1 partition, exact top-40 in 8.9 ms; **AC3 NOT MET**: the planner never uses the `(owner_id, tsv_en)` GIN (seq scan 2.7 ms, or PK bitmap when seq scans are disabled).
- Status: AC1, AC2 VERIFIED · AC3 UNVERIFIED (open; spike texts are too short to be representative; decision moved to S2.20, see followups F-006)
- Plan edits: AC commands now use the compose wrapper script (no host `psql` connection needed); AC2 counts *distinct* partitions (the original line count also matched the lexical plan); AC3 now requires the scan to be on the tsv_en GIN, not just any bitmap scan, so it can't pass on the PK.
- [T] changes approved: none

### S0.18 — Eval corpus wiring, test fixtures, provider limits
- Date: 2026-11-12 · Commits: b97058a (merged in 27f1087) · built by a parallel agent, reviewed and merged here
- Commands: `make eval-fetch` (from empty `eval/sources/`), `make eval-fetch eval-build && git diff --exit-code`, `make test-unit`
- Output: build items=122 docs=15 train=50 test=72 smoke=21, 0 gold not found, FinanceBench pages 14/14; git diff clean (golden/documents unchanged); fixture + corpus tests pass (hashes 15/15, gold located 100/100, fixtures deterministic, 7 upload types, 9 zoo cases). Without `eval/sources/` the corpus tests skip with a reason.
- Status: AC1–AC6 VERIFIED · AC7 UNVERIFIED (user action: confirm tier, set $200 Console spend limit, record OpenAI tier)
- Found: the original `fetch_sources.sh` silently kept a truncated CUAD zip (21.6/106 MB, exit 0). Rewritten to fail loudly and resume.
- Plan edits: AC3 now says "evidence-graded item" (summarize/meta items have no gold by design, so the old wording could not pass). `[tool.uv] default-groups = ["dev","eval"]` so fixture tests run in `make test`.
- [T] changes approved: none

### S0.21 — Spike: geometry transforms
- Date: 2026-11-12 · Commit: fb84346 (merged in 27f1087) · Evidence: `docs/evidence/spike_geometry/`
- Commands: `cd backend && uv run --frozen --group eval python ../scripts/spikes/geometry_spike.py --case {cropbox,rotate90,ligature}` (+ rotate270)
- Output: cropbox IoU 0.909 (uncorrected 0.000); rotate90 0.911/0.911 (both variants natural order); rotate270 0.911; ligature: 8/41 QASPER paragraphs matched exactly, 8/8 round-trip, all ligature glyphs mapped; exit 0 for all.
- Status: VERIFIED. **Finding: research 05 §3.3 is wrong for rotation** (see followups F-007).
- [T] changes approved: none

### S0.22 — Spike: collector config validation in grafana/otel-lgtm
- Date: 2026-11-12 · Commit: (this commit) · Notes: `ops/otel/README.md`
- Commands: `make otel-validate`; `make otel-validate CONFIG=ops/otel/tests/broken.yaml`
- Output: good config exit 0; config referencing an undefined processor exit 2 ("references processor does_not_exist which is not configured"). The image's collector is `/otel-lgtm/otelcol-contrib/otelcol-contrib`; our config is mounted over `/otel-lgtm/otelcol-config.yaml`.
- Status: VERIFIED
- [T] changes approved: none

### S0.15 — OpenTelemetry instrumentation and collector config
- Date: 2026-11-12 · Commit: (this commit)
- Commands: `make test-integration` (test_trace_propagation.py); `make test-unit` (test_collector_config.py); `make otel-validate`; `cd backend && uv run --frozen python ../scripts/check_trace.py` against the running stack
- Output: AC1 a FastAPI server span and the Celery worker span (real in-process worker over an in-memory broker, context via task headers) share one trace_id; AC2 validate exit 0; AC3 the three `gen_ai.*` attributes deleted in the traces and logs pipelines, ahead of `batch`, behind `memory_limiter`; AC4 request with a known trace id found in Tempo (service `vault-api`).
- Status: AC1–AC5 VERIFIED (AC5 `tests/unit/llm/test_providers.py::test_anthropic_otel_metadata_only` added after S0.13 merged: the SDK client is built with `content_mode=metadata_only`)
- Notes: setup is idempotent per process (the solo Celery pool fires `worker_process_init` in-process and had created real OTLP exporters inside tests). Tail sampling is defined but not in any dev pipeline. Tempo's HTTP port 3200 is published for the check script.
- [T] changes approved: none

### S0.17 — Content-leak guard harness
- Date: 2026-11-12 · Commit: (this commit)
- Commands: `make test-unit` (tests/unit/support/test_leak_guard.py); `make ci`
- Output: 3 passed — a planted string is caught when logged via structlog, caught when set as a span attribute, and clean logs/spans pass. Fixture `leak_guard` (tests/support/leak_guard.py) captures stdlib/structlog records and spans from its own tracer and from the global SDK provider if present; `assert_no_leak(secrets)` raises `LeakError` naming only the first 12 characters of a leaked string.
- Status: VERIFIED
- [T] changes approved: none

### S0.13 — LLM gateway core
- Date: 2026-11-12 · Commit: 080c933 (merged) · built by a parallel agent, reviewed and merged here
- Commands: `make ci`; `make test-integration`; `make test-e2e`; `make smoke-providers` (no keys)
- Output: AC1–AC12 named tests pass (one `llm_calls` row per attempt; retry-after honoured; 3-attempt cap; schema/refusal/max_tokens typed errors; cost incl. cache tokens; AST scan finds no provider client outside `app/llm`; citations+format refused before any call; effort from front-matter). Extra: 10 tests drive the real anthropic 2.8.0 / openai 3.49.0 clients over canned HTTP (request shape, citations, SSE `citations_delta`, error mapping). Merged tree: 61 unit, 46 integration, 3 e2e pass.
- Status: AC1–AC12 VERIFIED · AC13 UNVERIFIED (no API keys; `make smoke-providers` fails with a clear message)
- Design facts found (details in followups): Haiku 5.5 costs more above 100K prompt tokens; cited text must never go back as user content (400); Haiku cache-read and embedding prices are unconfirmed (overridable via `LLM_PRICES`).
- [T] changes approved: none

### S0.12 — [OPT] S3-compatible object store
- Date: 2026-11-12 · Status: SKIPPED (optional; the `minio` profile is defined in compose using SeaweedFS, see followups F-003). Not needed for any acceptance criterion before S10.

### S0.19 — Spike: live `search_result` citations
- Date: 2026-11-12 · Status: **BLOCKED / SUPERSEDED PENDING DECISION.** The user has only an OpenAI key (the assignment supplied it; no Anthropic key). The spike tests Anthropic-native citations, which cannot run. See followups F-015; the replacement spike depends on the provider decision.

### S0.26 — S0 Gate (partial)
- Date: 2026-11-12 · Commit: (this commit)
- Commands: `scripts/fresh_clone_check.sh`; `make ci`; `make test`
- Output: AC1 fresh clone of HEAD (7622584) built and all 8 services healthy; AC2 `make ci` passes; AC3 `make test` 111 passed (62 unit + 46 integration + 3 e2e); AC4 entries exist for S0.01–S0.22 (S0.12 skipped, S0.19 blocked).
- Status: AC1–AC4 passed mechanically before the provider change. The gate is **NOT closed**: the new tasks S0.23–S0.25 (OpenAI provider, model roles, citation spike) and S0.18 AC7 (user: spend limit) are open; its AC4 loop now covers S0.01–S0.25.
- Fixed on the way: the trace-propagation test left Celery on an in-memory broker and broke the e2e tests when run in one process (`make test`); it now restores the broker, AMQP and backend caches.
- Finding: OpenAI key models (from `GET /v1/models`): gpt-5.5, gpt-5.4(-mini/-nano/-pro), gpt-5.6-luna/sol/terra, gpt-5.2, gpt-5.1, gpt-5(-mini/-nano/-pro), gpt-4.1(-mini), gpt-4o(-mini), o3(-pro), text-embedding-3-small/large. Embeddings limit 10,000 RPM.
- [T] changes approved: none

### Provider change: OpenAI as sole runtime provider (design + plan rewrite)
- Date: 2026-11-13 · Commit: (this commit)
- Why: the user has only the OpenAI key supplied with the assignment; the spec allows "OpenAI API (GPT-4 + Embeddings API, or Claude)".
- Research: OpenAI pricing and model pages, structured-outputs, prompt-caching and streaming guides, plus probes on the project key (`docs/evidence/spike_openai.md`): strict schema with a handle enum gave 0 invalid citations in 6 calls; caching, rate limits (terra 15,000 RPM / 40M TPM), Flex and effort support measured.
- Design: §4 rewritten (roles `fast`/`balanced`/`deep`/`flagship` = `gpt-6-luna`/`gpt-6.1-terra`/`gpt-6.1-sol`/`gpt-6-astra`, routing table per node, effort support, role fallback, API facts); §5.3 gateway; §8.5 application-managed citations; §9.1 brief; §13 limiter and capacity; decisions D2, D12, D15, D22 revised, D26 and D27 added. `llm_calls` gains `role`, `resolved_model`.
- Plan: new tasks S0.23 (OpenAI Responses provider), S0.24 (roles, router, fallback), S0.25 (citation spike), S4.19 (stage E per-node routing optimizer); S0.19 demoted to [OPT]; S0.13 AC13, S0.18 AC7, S2.14, S2.18, S3.06–S3.09, S3.16, S4.09, S4.13, S5.02, S5.06, S6.02 rewritten; gate renumbered S0.26. `plan_lint`: 194 tasks, 792 ACs, 0 errors.
- Status: VERIFIED (lint, probes); nothing built on the new path yet.

### S0.23 — OpenAI Responses provider (also closes S0.13 AC13)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `make ci` (lint, mypy strict, 72 unit tests pass); `make smoke-providers`
- Output: `openai gpt-6-luna in=83 out=12 cost=$0.000014 latency=2378ms` (strict-schema call through the gateway) and `openai text-embedding-3-small in=3 cost=$0` (2 live calls, total under $0.0001).
- Built: `app/llm/providers/openai_chat.py` (`OpenAIResponsesProvider`, strict-schema transform, usage mapping that subtracts cached and written tokens from OpenAI's total input, refusal/incomplete handling, streaming), OpenAI GPT-6 prices incl. 1.25× cache writes and the >272K surcharge in `pricing.py`, `Effort` gains `none`, `ChatRequest.service_tier`; `get_gateway()` now uses it. The Anthropic adapter is untouched and rejects effort `none`.
- Not done here (S0.24): prompts still name `haiku_model` and the smoke test passes `model="gpt-6-luna"` explicitly until role routing lands.
- [T] changes approved: none

### S0.24 — Model roles, router and role fallback
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `make lint typecheck` (ruff, mypy strict clean); `make ci` (78 unit tests); `make test-integration` (49 tests, incl. migration round trip with 0002); `make smoke-providers` (2 live calls, ≈ $0.000014).
- Built: `app/llm/routing.py` (`ModelRouter`, default routing table from design §4.2, `EFFORT_SUPPORT`, `FALLBACK_ROLE`, `MODEL_ROUTES` overrides with specificity order `node:route:mode` → `node:route` → `node::mode` → `node`); settings `MODEL_FAST/BALANCED/DEEP/FLAGSHIP` + `MODEL_ROUTES` replace the three Claude model fields; prompt front-matter uses `role`; the gateway resolves through the router, records `role` and `resolved_model` (migration 0002), and makes one fallback attempt after retries on a retryable error (`CallResult.degraded = ("DEGRADED_MODEL_FALLBACK",)`). Attempt numbers run continuously across the fallback.
- Behavior changes to note: a pinned `model=` never falls back; the retry-cap tests pin a model. Router construction validates every row, so a bad `MODEL_*` value fails at startup and names the node.
- Not done: stream fallback (F-018).
- [T] changes approved: none

### S0.25 — Spike: application-managed citations on OpenAI
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `cd backend && uv run --frozen python ../scripts/spikes/citations_spike.py --run --max-usd 0.50` (17 calls, $0.054), then `--check enum_validity|segment_support|gold_cited|streaming|full_context_validity|cost`: all six exit 0. Report: `docs/evidence/spike_citations.md`; raw results: `docs/evidence/spike_citations_raw.json`.
- Output: 0 invalid handles in 12 RAG items; 15 of 15 segments carry a valid handle; the gold sentence was cited in 5 of 5 lookup-type items; 3 of 3 unanswerable items abstained; FULL_CONTEXT 0 of 4 handles invalid; two streams parsed incrementally and equal the non-streamed parse.
- Findings: (1) cache writes are billed by default (+25% on never-reused prompts); `prompt_cache_options.mode=explicit` removes it (F-021, design §4.4, S3.06 note). (2) The sample is small (F-022), so the targets are indicative only. (3) Provider stream now closes the HTTP stream deterministically (`async with`); an early `return` left an `aclose()` traceback.
- Also: `gateway.stream` accepts a `schema` (JSON fragments as deltas); `ChatRequest.cache_mode`; dev stack rebuilt and migrated to 0002.
- [T] changes approved: none (targets met on the sample; recorded as weak evidence).

### S0.26 — S0 Gate (final)
- Date: 2026-11-13 · Commit: (this commit) · Tested tree: a0db65a
- Commands: `TMPDIR=$CLAUDE_JOB_DIR/tmp scripts/fresh_clone_check.sh`; `make ci`; `make test`; the AC4 loop.
- Output: AC1 fresh clone of a0db65a built and all 8 services healthy; AC2 `make ci` exit 0 (ruff, mypy strict, 78 unit tests); AC3 `make test` 130 passed (78 unit + 49 integration + 3 e2e); AC4 loop printed nothing (S0.12 skipped and S0.19 demoted have entries).
- Live spend in S0 after the provider change: smoke ≈ $0.0001 per run, citation spike $0.054, probes < $0.01.
- **Open at the gate (not blocking the code path):** S0.18 AC7 (user records the OpenAI project spend limit), S0.20 AC3 (GIN index use at spike scale, F-006, re-measured in S2.20), F-014 (per-worktree test DB names before any parallel agent round).
- [T] changes approved: none

### S0.18 AC7 — Provider tier and spend (user)
- Date: 2026-11-13 · M (user decision)
- Tier evidence: response headers on the project key, `gpt-6.1-terra` 15,000 RPM / 40M TPM, `gpt-6-luna` 30,000 RPM / 180M TPM, embeddings 10,000 RPM (`docs/evidence/spike_openai.md`).
- Budget: **OpenAI project limit $50; convergence engine `BUDGET_USD` $30** (user, 2026-11-13; lowered from the suggested $200/$150). Spend to date ≈ $0.07. The dashboard limit itself is set by the user in the OpenAI console; the engine and spikes enforce their own caps in code.

### Plan change: core path and Milestone M0 (user decision 2026-11-13)
- After the S0 assessment the user approved building a working spine first: 51 core tasks in checkpoints C1 (ingestion), C2 (retrieval and measurement), C3 (chat and metrics = M0), with a review pass at each checkpoint. Defined in `docs/plan/README.md` → "Core path"; CLAUDE.md workflow step 8.
- Pulled in for a strong base beyond the minimal loop: S1.10 sweeper, S1.15 reading order, S1.16 tables, S1.17 PDF failure classes, S1.19 page quality, S2.09 neighbor expansion, S2.14 engine core (spend ledger), S2.18 judges.
- Closure: `python3 scripts/plan_core_closure.py` → `core tasks=51 unresolved={}`; `plan_lint` 0 errors.

### S1.01–S1.05 — Upload path (core path C1)
- Date: 2026-11-13 · Commit: (this commit). One commit for five tasks: they form one request path (S1.01 AC1 needs the S1.04 rows, the router needs S1.05), so separate commits would not each pass.
- Commands: `make ci` (84 unit); `make test` (157 passed: 84 unit + 70 integration + 3 e2e).
- Built: `POST /v1/documents` streaming multipart receiver (`app/documents/upload.py`, python-multipart's incremental parser, 413 mid-stream and on a declared oversize Content-Length, temp file always removed); magic-byte sniffing (`sniff.py`; PDF, DOCX by ZIP central directory without decompressing, UTF-8 text without NUL); content-addressed blobs with `put_file` (no full read into memory) and a single-statement refcount upsert; `create_from_upload` with a per-(owner, sha256) advisory lock for race-free dedupe; `Idempotency-Key` stored in the same transaction as the work (a concurrent duplicate blocks on the key and replays), canonical sorted-key JSON so replays are byte-identical; hourly beat purge (`vault.maintenance.purge_idempotency_keys`); `app/worker/runtime.py` runs async DB work from Celery tasks on a NullPool engine per task.
- New dependency: `python-multipart` 0.0.33. Why: FastAPI's form parsing buffers the whole body before the handler runs, so the 25 MB cap and bounded memory need the streaming parser, which FastAPI itself uses for forms.
- Evidence notes: streamed 25 MB upload grows the Python heap by 2.2 MB vs 25 MB buffered (probe), so the AC4 test discriminates (F-025: tracemalloc, not RSS). The first idempotency run failed on byte order of the replayed JSON (JSONB reorders keys); fixed with canonical serialization.
- Not yet: the job stays `pending` until S1.07 dispatch lands.
- [T] changes approved: none

### S1.07–S1.10 — Job pipeline: dispatch, claim/lease, stage runner, sweeper (core path C1)
- Date: 2026-11-13 · Commit: (this commit). One commit: the runner's commit path dispatches (S1.07), claims (S1.08) and is recovered by the sweeper (S1.10); the tests exercise them together.
- Commands: `make ci` (86 unit); `make test` (178 passed: 86 unit + 89 integration + 3 e2e).
- Built: `app/ingestion/{jobs,runner,sweeper,status,errors,stages}.py`, Celery tasks `vault.ingest.stage` / `vault.ai.stage` (payload = job id), beat `sweep_jobs` (60 s) and `purge_job_events` (daily, 90 days); upload dispatches after commit (CAS, so idempotent replays and dedupe hits never enqueue twice).
- Design corrections (written into design §6.6 and §14): (1) the §14 dispatch CAS as written would block every next-stage dispatch and every retry (fresh `last_dispatched_at`, live lease), so stage commit/failure now clear both and the CAS also honours `next_attempt_at`; (2) the stage commit is fenced by the lease (`FOR UPDATE … lease_owner = me`), so a worker that lost its lease writes nothing; (3) the extract output is handed to later stages via `extracted/{version_id}.json.gz` (design §6.3 named no location).
- Status machine: the table is checked against an independently written §6.2 edge list plus 2,000 random walks (seeded; no new dependency).
- Not yet: no stage is implemented, so a real upload sits at `extract` failing retryably with `stage_unavailable` until C1c lands.
- [T] changes approved: none

### S1.12 — Extractor protocol and TXT/MD extractor
- Date: 2026-11-13 · Commit: (this commit) · Command: `pytest tests/unit/extract/test_text.py` (3 passed); `make ci` green.
- Built: `app/ingestion/extract/base.py` (`ExtractedDocument`/`PageText`/`Element`, gzip-JSON handoff, `check_offsets` invariant: every element is an exact slice of its page text), `text.py` (headings → `section_path` stack, blank-line paragraphs, list items, pipe tables, code fences kept whole; UTF-8 with U+FFFD fallback counted).
- [T] changes approved: none

### S1.13 — PDF glyph stream and words (invariant I1)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `pytest tests/unit/extract` (26 passed, incl. the 9-file geometry zoo); `make eval-extraction` → `located_pct 100.0` (94/94 gold quotes over 12 corpus PDFs; the S0 sweep baseline was 99/100 on an earlier quote set).
- Built: `app/ingestion/extract/glyphs.py` (transforms and normalization ported from the S0.21 spike: CropBox offset, /Rotate via the upright text frame, NFKC per glyph with every expanded char mapped to the same glyph) and `pdf.py` (lines → headings/paragraphs; page text assembled from glyph-mapped lines with synthetic `\n`/` ` only; glyph boxes normalized to the rendered page). The coordinate normalization of S1.14 is included because the spike had validated it; S1.14's own ACs stay open (deferred task).
- Heading levels: font-size rank, with structural/all-caps headings (`ITEM 1A.`, `ARTICLE 5`, `SECTION 2.1`) one level above mixed-case headings of the same size. Without that, AMD's "Risk Factors Summary" did not nest under "ITEM 1A. RISK FACTORS".
- AC4 note: the phrase is on PDF page 3 (index 2), not page 2 as the AC says; the test cites page 3.
- Measured cost: AMD 10-K (121 pages) and 3M 10-Q extract in ≈ 38–41 s each, within the 120 s parse timeout but the slowest step; recorded as F-026.
- [T] changes approved: none

### S1.15 — Reading-order safety (AC1–AC3; AC4 lands with the quality stage, S1.19)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `pytest tests/unit/extract/test_reading_order.py` (3 passed); `make eval-extraction` → 100.0 (94/94) after the fixes below.
- Built: `app/ingestion/extract/reading_order.py`: gutter split (gap > 1.5 em), column repair (1-D k-means k ∈ {2,3}, silhouette ≥ 0.6, centres ≥ 25% page width apart, each column ≥ 15% of lines and prose-width lines), footnote separation (font < 0.85 × body median in the bottom 20%) as `footnote` elements.
- Two corpus regressions caught and fixed before commit: (1) financial-statement tables were detected as columns and re-ordered (locatability fell to 94.7%; fixed by requiring prose-width columns); (2) on QASPER 1910.03814 p.2, text inside a figure triggered a repair that spliced tweet fragments into body paragraphs (fixed by requiring column jumps on ≥ 25% of lines, which true interleaving meets and figure text does not). Corpus now: 0 repairs, 7 paper pages with footnotes separated; the interleaved fixture is repaired.
- [T] changes approved: none
