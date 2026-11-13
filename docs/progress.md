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

### S1.16 — PDF tables (AC1, AC2; AC3 is chunk-level and is ticked with the chunker, S1.22)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `pytest tests/unit/extract/test_pdf_tables.py` (3 passed); `make eval-extraction` → 100.0 (94/94).
- Built: `app/ingestion/extract/pdf_tables.py`: ruled tables via `find_tables` become pipe-table rows whose cell text is built from the glyph-mapped words (I1 holds; only ` | `, spaces and newlines are synthetic), spacer columns dropped, table words removed from the paragraph flow, tables placed by vertical position; a table that opens a page and matches the previous page's closing table (same column count) inherits its header (`Element.table_header`).
- Finding: the synthetic ` | ` separators made 3 flattened financial-statement gold quotes fall below the fuzzy threshold (96.8%). The locatability check now compares with separators treated as whitespace (they are formatting, not document text) → 100%, which also confirms cell order.
- Also in this commit: F-014 closed (integration tests use `vault_test_<checkout hash>`); CLAUDE.md "Parallel work" protocol (user instruction to parallelize throughout); F-027 (repo on iCloud Desktop; worktrees redirected outside iCloud).
- [T] changes approved: none

### S1.18 — DOCX extractor with bomb limits (worker lane, Sonnet)
- Date: 2026-11-13 · Commit: 4f00233 (lane branch, merged fast-forward)
- Merge gate (main session): diff reviewed; `make ci` 123 passed; `make test` 216 passed on the merged tree.
- Built: `app/ingestion/extract/docx.py`: zip metadata check (100 MB total, 100:1 per member, reads no member data) before python-docx; body walked in order (paragraphs and tables); `Heading N` → section stack like text.py; tables as pipe rows from the real `w:tc` elements (merged cells not duplicated); corrupt archives → permanent `corrupt_file`. AC3 runs the zip bomb through the real runner (dead, version `failed/unsafe_archive`, < 2 s).
- Known limits (lane report): nested tables, text boxes, footnotes and `w:sdt` content ignored; only `Heading N` style names recognised (localized/`Title` styles become paragraphs); size caps trust zip headers.

### S1.21 — Sentence segmenter (worker lane, Sonnet)
- Date: 2026-11-13 · Commit: f67b626 (merged in d15e2c1)
- Merge gate: diff and fixture reviewed (50 cases: 15 legal, 15 financial, 10 academic, 10 edge; written before the code, not tailored); `make ci` 131 passed on the merged tree.
- Result: AC1 49/50 [T ≥ 48]; the miss is the deliberately ambiguous "…in the U.S. Europe was flat." (soft abbreviation followed by a non-opener). AC2 partition and AC3 clause numbers pass.
- Design: hard abbreviations never split (Mr, No., Sec., Fig., months, e.g./i.e.); soft ones (Inc., Corp., U.S., etc., initials) split only before a typical sentence opener or an enumerator; clause labels ("Section 3.1.", "4.2(b).") stay with their sentence; blank line always splits, single newline never; `kind="table"`: one sentence per row.

### S1.17 — PDF failure classes and parser timeout; S1.19 — page quality (AC1, AC3, AC4); S1.15 AC4
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `make ci` (132 passed); `make test-integration` (97 passed) incl. `test_pdf_failures.py`, `test_quality.py`, `test_reading_order.py`.
- Built: `ExtractStage` runs the extractor in a subprocess (`python -m app.ingestion.extract.worker`): killable on `PARSE_TIMEOUT_S` (→ permanent `parse_timeout`), address space capped by `PARSE_MEMORY_MB` on Linux (new setting), StageErrors returned as JSON (exit 3), crashes retried; the ExtractedDocument goes to `extracted/{version_id}.json.gz`. PDF failure classes: pdfminer `PDFPasswordIncorrect`/`PDFEncryptionError` → `unsupported/encrypted`; anything unparseable → `failed/corrupt_file` (no retries). `QualityStage` writes `pages` rows (text chars, quality score, extractor flags + `needs_ocr` < 50 chars + `garbled` > 5% replacement/cid) and the version `quality` summary; ≥ 80% OCR pages → terminal `needs_ocr` (job succeeded, zero chunks).
- S1.19 AC2 (mixed document reaches `ready`) is open until chunk/embed/index exist; today the test shows the job continues past quality with page 5 flagged.
- [T] changes approved: none

### S1.27 — Document read endpoints; S1.26 — Delete and purge (worker lane, Sonnet) + main-session hardening
- Date: 2026-11-13 · Commits: 822f78b (S1.27), bab6a91 (S1.26) on the lane branch, merged in 37940a1; hardening in this commit.
- Merge gate: diff reviewed (owner scoping on every query, 404 for other owners on all six read endpoints, purge locks the version row and decrements refcount once per version in the same transaction); `make ci` 132 and `make test` 254 passed on the merged tree.
- Endpoints: `GET /v1/documents` (keyset cursor on created_at,id; status/q/tag/category), `GET /v1/documents/{id}`, `GET /v1/versions/{vid}/processing` (job_events timeline + page flags), `/file` (sha256 ETag), `/pages/{n}/layout` (reads the deferred S1.20 layout format; 404 when absent), `GET /v1/chunks/{id}`, `DELETE /v1/documents/{id}` (204, purge enqueued after commit). The runner's fenced commit now aborts a stage of a deleted document (`dead/deleted`, no outputs).
- Hardening by the main session: (1) the lane marked purged-but-ledger-referenced versions inside the `quality` JSON; replaced by a proper `document_versions.purged_at` (migration 0003); (2) the purge enqueue is not transactional, so a lost enqueue left a document unpurged forever: new beat task `purge_pending` (every 10 min, 10-min grace) with `test_lost_purge_recovered`.
- Open risks (lane report): file endpoint loads ≤ 25 MB into memory (no streaming get on the store); a re-upload racing a purge at refcount 0 could find the blob object deleted; `_fail` on a deleted document still marks the version failed (rows are purged anyway).

### S1.22 — Chunker `chunker@1` (also S1.16 AC3 at chunk level)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `make ci` 143 passed (exit 0; slow run, machine load 43 from a concurrent reranker benchmark); `make test-integration` 120 passed; `make eval-extraction` 100.0 after the running-header change.
- Built: `app/ingestion/chunk.py`: sentence units (S1.21 segmenter) packed within a section group to 400 tokens, max 800, ~15% whole-sentence overlap, small remainders merged; heading-only groups dropped (the heading lives in the children's prefixes); tables and footnotes standalone (document order kept); oversize tables split by rows with the header row repeated, including a header carried from the previous page; pieces are exact page-text slices with `spans`; `structural_prefix` `Document: … | Section: … | Page: n[-m]` (no Page part for flow documents, page 0 in the NOT NULL columns); `embedding_text = prefix + "\n" + original_text`; `content_hash = sha256(embedding_text + "chunker@1")`. `ChunkStage` replaces the version's chunk rows and records `chunker_version` and `token_count`.
- Also: running headers/footers (lines repeated on ≥ 50% of pages in the top/bottom 10% band, digits normalized) are removed from the element flow; AMD's "Table of Contents" on every page was leaking into chunks.
- Measured: AMD 10-K: 494 chunks (59 tables), max 791 tokens, median 356 on the narrative excerpt (pp. 15-45) but 166 over the whole filing (short note sections, which AC1 forbids merging); QASPER 1910.03814 median 284; CUAD distributor median 220. Spans: 0 mismatches over 581 chunks.
- New dependency: `tiktoken` (cl100k_base, the embedding model's tokenizer) for token counts. Its vocabulary download (83 s on first use) is baked into the Docker image (`TIKTOKEN_CACHE_DIR=/opt/tiktoken`) and cached at `~/.cache/tiktoken` for host runs (Makefile `HOST_ENV`).
- [T] changes approved: none; whole-filing median below 300 recorded as F-032 for the convergence engine (stage B).

### S2.07 — Reranker service (worker lane, Sonnet; AC1–AC5; AC6 open)
- Date: 2026-11-13 · Commit: aad325d (merged in 1b1aa51)
- Merge gate: diff reviewed (allowlisted models with recorded licenses, semaphore admission with 800 ms queue timeout → 503 `overloaded`, inference in a bounded thread pool, slots released when the thread really finishes, request limits → 422, spans carry counts/model only); reranker tests 7 passed; stack rebuilt healthy.
- AC6 not met *as measured*: all runs were under heavy machine load (iCloud sync, Docker VM): MiniLM p95 2.4–3.9 s, bge 20 s. Re-measure on an idle machine (F-033). License question for MiniLM (MS MARCO training data) recorded for the user.

### Infra: repo moved off the iCloud Desktop
- Date: 2026-11-13. Repo now at `~/dev/vault` (rsync copy, git fsck clean, same tracked files); symlink at the old Desktop path; old folder renamed `…old.nosync`. Memory copied to the new project key. Stack rebuilt from the new path: all 8 services healthy; `make ci` 143 passed (80 s vs 398 s before); `make test` 266 passed.

### S1.24 — Embed stage; S1.25 — Index stage (core path C1)
- Date: 2026-11-13 · Commit: (this commit)
- Commands: `make ci` 143 passed; `make test-integration` 129 passed (exit 0).
- Embed (`app/ingestion/embed_index.py`): chunks without an embedding first copy vectors from this owner's already-embedded chunks with the same `content_hash` and model (re-uploads and new versions pay nothing for unchanged chunks); the rest go through `gateway.embed` in batches of ≤ 128 (lane `background`, `purpose=embed`, ledger rows carry the job id); each batch is persisted as soon as it returns, so a failure after batch 1 keeps batch 1 and the retry only pays for the rest (test: 300 chunks, batch 2 fails → 128 kept, retry makes 2 calls of 128 and 44). Provider errors → retryable stage error; a rejected input → permanent `embedding_rejected`.
- Index: requires ≥ 1 chunk (else permanent `no_text`) and every chunk embedded (else retryable); then, in the fenced commit, locks the version and document rows, marks the previous current version `superseded`, moves `current_version_id`, calls the staleness hook once (a redelivered stage does nothing), and the runner sets `ready` + `ready_at`. A v2 that fails leaves v1 current and `ready`.
- Also: `build_gateway(sessionmaker)` so Celery tasks get a gateway bound to their own per-task engine (the cached API engine cannot be shared across event loops); `app/insights/staleness.py` no-op hook (S6.09); test cleanup deletes `llm_calls` before jobs.
- [T] changes approved: none

### S3.01–S3.04 — chat sessions, turn ownership, message dedupe, memory window (worker lane, Sonnet)
- Date: 2026-11-13 · Commits on lane branch, merged aa65341. Merge gate: `sessions.py` read in full; `make ci` 148 and `make test` 309 passed (19 skipped = opt-in corpus and unfetched sources) on the merged tree.
- Built: `POST/GET /v1/chat/sessions`, `POST /v1/chat/sessions/{id}/messages` (creates the user message and a `running` run in the same transaction as the lease claim; retried `client_message_id` returns the same turn; different content → 422), CAS lease with `run_id`-fenced release and takeover after expiry, memory window (whole turns, ≤ 3k tokens, rolling summary included). Concurrency is tested for real (8 concurrent claims → 1 winner; 6 concurrent identical posts → 1 turn).
- Open: S3.02 AC4 (no DB connection held across a generate call) needs the generate node (S3.08).
### S2.01 — Scope; S2.05 — Lexical search (AC1, AC3–AC5; AC2 open) (worker lane, Sonnet)
- Merged 086f5ad. Scope = owner's live docs at a ready current version; narrowing only (`ScopeWideningError`); `SCOPE_EMPTY`. Lexical: `websearch_to_tsquery` on `tsv_en` + `simple` query over identifier-like tokens on `tsv_ids`, stable tie-break, always bound parameters and owner-filtered. EXPLAIN at 3,000 + 3,000 chunks: owner partition pruning confirmed; the GIN index is used only after the pending list is flushed (fresh rows sit in the pending list and inflate its planner cost).
- S2.05 AC2 stays open: `tsv_ids` is populated by S1.23 (deferred); the lane's test used a stand-in.
### C1 checkpoint run (`make c1-check`)
- All 15 corpus documents (CUAD ×6, QASPER ×3, FinanceBench ×3, authored ×3) upload → extract → quality → chunk → embed (FakeProvider) → index and reach `ready`: chunk counts 1 to 429; all chunks embedded; no text chunk over 800 tokens. The 4 failure fixtures end as `unsupported/encrypted`, `failed/corrupt_file`, `needs_ocr`, `failed/unsafe_archive`, and no text reaches the embedding provider. 19 passed in 226 s.

### S3.06 — Evidence set, handles, answer schema, manifest; S3.09 — Citation resolver
- Date: 2026-11-13 · Commit: (this commit) · `pytest tests/unit/chat` 15 passed (7 + 5 named ACs plus extras); mypy strict and ruff clean.
- Built: `app/chat/evidence.py` (handles `E<k>.<i>`, header `[E<k>] {title} v{n} · p.{a}-{b} · {section}`, DOCX chunks without a page part, no prefixes in the evidence text, manifest handle → chunk/sentence, strict answer schema with a handle enum in RAG mode and plain strings when handles exceed 1000, `cache_mode_for`: RAG `explicit`, FULL_CONTEXT default) and `app/chat/citations.py` (handles resolved through the manifest, adjacent sentences of one chunk merged into half-open ranges, `cited_text` cut by code from stored `original_text`, unknown handles dropped with `CITATION_UNRESOLVED` and counter `vault.citations.unresolved`, segments without a valid handle kept as `support=none`).
- Verified through the real strict-schema transform (`strict_schema`): the RAG enum equals the handles present; FULL_CONTEXT items are plain strings; exactly 1000 handles still use an enum and 1001 fall back.
- S3.09 is pure; it was built ahead of S3.08 (generate node) because it has no dependency on it.

### C1 checkpoint review pass (independent Sonnet reviewer, read-only) and fixes
- Date: 2026-11-13. The reviewer read ingestion, upload, delete, chat sessions and retrieval scope/lexical and reported 16 findings, none of which the existing tests caught (authors wrote both code and tests). No cross-owner leak was found. Each fix below has a regression test; `make test-integration` 168 passed, `make ci` 162 passed.
- **Fixed (HIGH)** #1 errors inside the fenced commit were never counted: a failing stage `write` left the job `running`, the sweeper redelivered it, and it failed identically forever. Now counted like any stage failure (`commit_failed`, retried, then `dead` + version `failed`); a takeover of an expired lease also counts as an attempt (`worker_lost` after max attempts, so an OOM-ing stage can no longer loop). Logs no longer carry SQL parameter dumps (document text): only error type and file:line.
- **Fixed (MEDIUM)** #2 purge vs same-bytes re-upload (the upload re-asserts the blob file after commit); #3 quadratic sentence splitter (224 KB went from 71 s to well under 1 s); #4 DOCX text inside tracked insertions, hyperlinks and content controls was silently dropped (`para_text` + `iter_blocks`; deletions stay hidden); #5 headings with inner whitespace/tabs vanished from chunk text (one normalized form in all extractors); #6 upload size now capped on total streamed bytes even without Content-Length, malformed multipart → 422; #7 CJK/no-space text: full-width terminators end sentences and `_hard_split` falls back to a token-bounded window (chunks stay ≤ 800).
- **Fixed (LOW)** #8 a failing heartbeat no longer masks the stage result, and a genuinely lost lease cancels the stage (no more paid calls after losing the job); #9 the extracted artifact is written inside the fenced commit (and the parser subprocess is killed on cancel); #11 the parser subprocess gets a minimal environment (no API keys or DB URLs); #12 control characters stripped from filename/title; #13 PDF page text assembly is linear; #15 stage queries filter by `owner_id`; #16 no paid embedding for a document deleted meanwhile, purge locks the job row first (matches the runner's lock order), quality scoring runs off the event loop. NUL glyphs are dropped at extraction (Postgres rejects NUL).
- **Deferred, recorded:** #10 the layout file (`layouts/{vid}.json.gz`, task S1.20) is not written yet, so `GET …/pages/{n}/layout` returns 404 in production until the highlight phase (F-037); #14 no cap on pages/memory per document and `RLIMIT_AS` is Linux-only (F-038).

### C2 lanes merged (worker lanes, Sonnet): S2.02, S2.03, S2.06, S2.08, S2.09, S3.05 (AC1, AC3, AC4), S7.01–S7.03; main: S3.07
- Merge gate: `make ci` 192 passed, `make test` 400 passed (19 opt-in skipped), corpus checks after the review fixes: `make eval-extraction` located_pct 100.0, `make c1-check` 19 passed.
- S2.02/S2.03: query embedding cached in Redis as float16 (7 d, degrades to lexical-only on provider failure, bypasses a down Redis); exact scoped cosine search with per-document quota, owner-partition pruning confirmed (1,500 rows: 6.5 ms plan, 23.6 ms end to end). S2.06/S2.09/S2.08: RRF with identifier boost and provenance, owner-filtered neighbor expansion within the token budget, rerank client that degrades (never raises) on timeout/503/connection error, verified against the real reranker. S3.05: `analyze_query@1`, live 8/8 correct for $0.0007 (S3.05 AC2, the 5-of-6 follow-up check, is not run). S7.01–S7.03: three metrics endpoints, owner-scoped, admin `scope=global`, hand-computed expectations, 17–71 ms at 5,000 documents. S3.07: `answer_rag@1` prompt (7-part skeleton), `output: segments` registered in the prompt registry and gateway.
- Progress after this merge: 301 of 792 acceptance criteria ticked (38%); core path 183 of 236 (77.5%).

## 2026-11-13 — C2/C3 spine merged; first real smoke eval
- Merged lanes E (S3.08), F (S3.10/S3.11), G (S2.12/S2.13); wrote S2.10 `retrieve()`, the turn orchestrator (`app/chat/turn.py`, `POST /messages` now answers), `scripts/demo_core.sh` (live OK, $0.0019).
- Gate: `make ci` 235 passed, `make test` 471 passed. Lanes S2.14 (engine) and S2.18 (judges) running.
- Real smoke eval (`eval.pipeline_http:setup`, 21 items, ~$0.10): recall@8 0.81, mrr@40 0.66, citation_integrity 1.00 (A met), cite_chunk_precision 0.74, abstain_recall 1.0, conflict_correct 0.0 and meta_correct 0.0 (routes not built yet: expected). Report: `eval/reports/01a1d428-72b0-733f-8119-90d3f9449ea7.md`. Regenerate: `cd backend && PYTHONPATH=.. uv run --frozen python -m eval.run --suite smoke --config baseline_smoke --pipeline eval.pipeline_http:setup`.
- S2.14 engine, S2.18 judges (live inter-judge agreement 1.00 on all four types, $1.11) and S2.17 merged. `make eval-retrieval` (122 items, ~$0.70): recall@8 **0.759** (target >= 0.85 [T] NOT met, pre-review), mrr@40 0.505; weak: table 0.40, compare 0.55, conflict 0.58, identifier 0.67, lookup 0.82. Baseline frozen `eval/baselines/retrieval.json`. Report `eval/reports/01a1d43d-2bfe-7691-a250-7e3cd71c1d8f.md`. Regenerate: `make eval-retrieval EVAL_ARGS="--freeze retrieval"`. S2.17 AC3 stays open; tuning belongs to S2.19/S4 (convergence engine), not hand-tuning on test items.

## 2026-11-13 — S16.02 reranker configuration (root-cause fix)
- Service now: threads from cgroup quota (cap 4), batch 8, max_in_flight 1, passages truncated to 1200 chars at a word boundary (no 422), `/healthz` reports threads and batch. Tests: reranker `pytest tests` 11 passed (`test_cpu_quota`, `test_defaults`, `test_long_passage_truncated`, `test_overloaded_503`).
- Native bench (idle, load 3.4): `python bench.py --threads 4 --batch 8 --words 180 --n 40` -> p50 260 ms, p95 264 ms, peak RSS 354 MB (targets 350 ms / 450 MB met). Old config: 1031 ms, 1.5 GB.
- AC6 (container bench via `make up-rerank`) pending: Docker is off by design.

## 2026-11-13 — S16.03 query-focused rerank windows
- `app/retrieval/windows.py::focus_window` (whole-sentence window <= 180 words maximizing distinct query stems; FirstP fallback; over-long sentence cut) and `Retriever._passages` now sends `title | section` + window (<= ~1.2k chars) to the reranker; `rerank.window_words` in config (default 180).
- Evidence: `make ci` 320 passed (4 new unit tests in test_windows.py). AC5 (`test_rerank_passages_windowed`, integration) written but NOT run: Docker is off; run it at S16.01 with `make up-test`.

## 2026-11-13 — S16.06 README skeleton and quickstart
- `README.md` rewritten (honest "What works today"), `scripts/quickstart.sh` (Docker check, .env, `make up`, migrate, demo user). Tests: `backend/tests/unit/docs/test_readme_skeleton.py` 2 passed. AC3 (real quickstart run) pending Docker.

## 2026-11-13 — S16.01 compose profiles and limits (file work; Docker steps pending)
- docker-compose.yml: every service has `mem_limit` + `cpus`; `reranker` (profiles rerank, full) and `otel-lgtm` (obs, full) are no longer default; ingest worker `-c 1`, AI worker `-c 2`. Default stack limit sum = 4.4 GB. Makefile: `up-test`, `up-rerank`, `up-full`, `down` stops all profiles; `scripts/wait_healthy.sh` takes services / COMPOSE_PROFILES. `docs/dev-environment.md` written.
- Evidence: `make ci` 322 passed; `tests/unit/ops/test_compose.py` 2 passed.
- PENDING (need Docker, user action): AC3 `make up-test`, AC4 `make up` + readyz `degraded`, AC5 user sets Docker Desktop to 6 GB / 4 CPUs, AC6 merged-lanes gate (`make test`).

## 2026-11-13 — S16.05 extraction time and memory (root cause)
S16.05 profile (cProfile, AMD 10-K, 121 pages, native): `pdfplumber.Page.dedupe_chars` = 93% of cumulative time (its final `sorted(deduped, key=chars.index)` is O(n^2); ~25 s) on top of pdfminer parsing (~25 s under profiling). Memory: reading chars alone peaks 71 MB, but our retained `Line.words` held every glyph dict (~1.6 KB/glyph) -> 5.5 MB/page growth.
Fixes: `glyphs.dedupe_chars_fast` (same selection, original order via id->index map; verified identical on 5 sample pages), and `ln.words = []` once reading order is done; `max_pages` setting (600) -> `unsupported/too_many_pages`.
Before/after (AMD 10-K, native, same 420251 chars): 37 s / 720 MB -> 11.3 s / 198 MB. `make eval-extraction` located_pct 100.0; `make ci` 326 passed; tests/integration/ingestion/test_extract_budget.py 3 passed. AC6 (`make c1-check`) pending: Docker image build (stalled uv downloads; added UV_HTTP_TIMEOUT/CONCURRENT_DOWNLOADS).
