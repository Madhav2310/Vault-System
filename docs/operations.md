# Operations

Running Vault on a laptop, what it costs, how it behaves under load and retries, how it scales to thousands of documents, what it emits for observability, and a script for demonstrating it.

Contents: [1. Dev environment](#1-dev-environment) · [2. Scale: measured and derived](#2-scale-measured-and-derived) · [3. Queueing, load and idempotency](#3-queueing-load-and-idempotency) · [4. Cost](#4-cost) · [5. Growing beyond one machine](#5-growing-beyond-one-machine) · [6. Observability](#6-observability) · [7. Demo script](#7-demo-script)

Rules for the numbers on this page: each one has a source in the last column (a command that regenerates it, or the file it comes from). A row that was not measured says `TBD` and names what would measure it. Derived numbers say how they are derived. **No load test has been run**, so nothing here is a concurrency or throughput claim beyond the single-document measurements listed. Measurements come from a 16 GB M4 laptop, mostly native, some in capped containers.

---

## 1. Dev environment

Docker runs a Linux VM that keeps its memory. Keep it small, and off when nothing needs it.

**Docker Desktop settings** (Settings, Resources): CPU limit 4, memory limit 6 GB, swap 1 GB, Resource Saver on. Applying them restarts Docker Desktop.

| Work | Command | Containers |
|---|---|---|
| Lint, types, unit tests, frontend checks | `make ci` | none |
| Integration tests | `make up-test`, then `make test` | postgres, redis (about 1.3 GB) |
| Running the app, the demo, evaluations | `make up` | plus api, workers, beat (about 4.4 GB limit) |
| Reranker measurements | `make up-rerank` | plus reranker (768 MB) |
| Observability (Grafana, Tempo) | `make up-full` | plus otel-lgtm (1 GB), heavy |
| Stop everything | `make down` | |

Every service has `mem_limit` and `cpus` in `docker-compose.yml`: api 1.5 CPU and 768 MB, worker-ingest 2 CPU, worker-ai 1.5, postgres 2 (with `fsync=off`, for dev and test only), redis 0.5 (no persistence, because its state is rebuildable), reranker 3, otel-lgtm 1, beat 0.25.

Working rules: Docker stays off unless a task needs it; run one heavy command at a time; check `uptime` first and wait if the load average is above about 8; use targeted tests (`pytest path::test`) while developing and the full gates once per change set; stop optional containers when idle (`docker compose stop otel-lgtm reranker`; retrieval keeps fusion order without the reranker).

**Fresh-clone check.** `scripts/fresh_clone_check.sh [--no-stack|--stack] [PATH_OR_URL]` clones the repository (committed files only) into a temp dir and runs the reviewer path there: prerequisites (git, make, uv; with `--stack` also Docker and curl), `.env` from `.env.example`, `make ci`. It prints a pass/fail table and exits non-zero on any failure. `--no-stack` is the default and needs no Docker. `--stack` also runs `make up`, waits for `/readyz`, runs `scripts/demo_core.sh` and runs `make down`; stop your own stack first (shared ports), and export `OPENAI_API_KEY` for the smoke step (it is written to the clone's `.env`, never printed; without it the step is reported as SKIP). The frontend checks inside `make ci` are skipped in a clone, because the frontend dependencies are not installed there.

Without an OpenAI key, `make ui-e2e` runs the UI tests against a scripted fake model on its own ports, and `scripts/demo.sh` runs against that stack with `LLM_PROVIDER=fake` (instructions at the top of the script).

---

## 2. Scale: measured and derived

Why exact search: one owner's corpus is thousands to tens of thousands of chunks. Exact search has perfect recall, and a global approximate index would cost RAM and lose recall under owner filters. Chunks are hash-partitioned by owner, so a scoped query touches one of 32 partitions. Only single-owner cases were measured; a many-owner dataset was not.

### 2.1 Ingestion: time and memory per document

| Measure | Value | Source |
|---|---|---|
| PDF extraction, AMD 10-K (121 pages, 420,251 characters), with the library's quadratic de-duplication | 37 s, 720 MB peak | profile `ingestion.extract.pdf` on `eval/sources/fb_pdfs/AMD_2022_10K.pdf` (not a make target) |
| Same document with the de-duplication replaced | 11.3 s, 198 MB peak | the same profile of `eval/sources/fb_pdfs/AMD_2022_10K.pdf` |
| Extraction seconds per page | 0.093 s/page (derived: 11.3 s / 121 pages) | arithmetic on the row above |
| 19-document corpus to `ready` | 36 s (113 s before the extraction fix) | `make c1-check` |
| Page cap | 600 pages, then `unsupported` (`too_many_pages`) | `backend/app/core/settings.py` (`MAX_PAGES`) |
| Embed batches in flight per document | 4 (`EMBED_CONCURRENCY`); 8 batches of 0.5 s finish in 2.0 s or less against 4 s or more sequentially | `cd backend && uv run pytest tests/integration/ingestion/test_ingest_concurrency.py` |
| Brief map calls in flight per document | 3 (`BRIEF_CONCURRENCY`) | `backend/app/core/settings.py` |
| AMD 10-K time to `ready` (searchable): sequential versus parallel embedding | 28.8 s versus 20.2 s (-30 percent); time to `enriched` is 57.6 s versus 57.4 s (one brief call, so map concurrency does not apply) | upload the 10-K with `EMBED_CONCURRENCY=1 BRIEF_CONCURRENCY=1`, then with the defaults, polling `GET /v1/documents/{id}` |
| Where ingestion time goes, 45 real documents | brief 16.7 s p50 and 52 s p95 (80 percent of stage time, after the document is already searchable); embed 1.7 and 5.4 s; extract 0.7 and 13.8 s; chunk, quality and index under 0.03 s | `select stage, percentile_cont(0.5) within group (order by duration_ms) from job_events group by stage` |
| Documents per hour per `worker-ingest` | TBD (needs a corpus run on `make up`) | none yet |

Derived estimate, extraction only: one ingest worker at 0.093 s/page needs about 9,300 s (2.6 h) for 1,000 documents of 100 pages, ignoring embed and brief (which run on the separate `ai` queue and overlap). `worker-ingest` runs one process (`-c 1`), so two workers halve it. This is arithmetic on a measured row, not a measurement.

### 2.2 Chunks per document

| Measure | Value | Source |
|---|---|---|
| Chunks, AMD 10-K (59 of them tables) | 494 | `make c1-check`, then `select count(*) from chunks where version_id = ...` |
| Chunk counts across the 15-document corpus | 1 to 429 | `make c1-check` |
| Corpus median chunks per document | TBD | `select count(*) from chunks group by version_id` after `make c1-check` |
| Median chunk size | 356 tokens on narrative 10-K pages, 166 over the whole filing (financial notes are many short sections, and chunks never cross sections) | token counts per row of the `chunks` table after `make c1-check` |

The two AMD figures (494 and a 429 maximum) come from different extraction versions. Re-measure before quoting either as current.

### 2.3 Search

| Measure | Value | Source |
|---|---|---|
| Exact top-40 vector query on a 21,000-chunk owner | 8.9 ms, pruned to 1 of 32 hash partitions | `EXPLAIN (ANALYZE)` of the scoped query in `backend/app/retrieval/vector.py` on a seeded owner |
| Scoped vector search, 1,500 rows | 6.5 ms plan, 23.6 ms end to end | the `EXPLAIN (ANALYZE)` above |
| Switch point to an approximate index | 100,000 chunks in one scope (a design default, not a measured crossover) | design default; `backend/app/retrieval/vector.py` is exact search only |
| Exact search latency at 100,000 chunks in one scope | TBD | the `EXPLAIN` above on a seeded 100,000-chunk owner |
| Reranker, 40 passages, native, 4 threads, batch 8 | p50 260 ms, 354 MB peak | `cd reranker && python bench.py --threads 4 --batch 8 --words 180 --n 40` |
| Reranker in its container (3 CPUs, 3 threads, 20 passages) | p50 350.8 ms, p95 578 ms, 230 MB | `make up-rerank`, then `python reranker/bench_http.py` |
| Retrieval-only evaluation, 100 items, warm | 145 s, about $0.0001 | `make eval-retrieval-fast` |

---

## 3. Queueing, load and idempotency

Behaviour under load is **not measured**; these are the mechanisms.

| Mechanism | What it does | Where |
|---|---|---|
| Job claim with lease and heartbeat | A duplicate delivery collapses. A worker that loses its lease commits nothing. | `backend/app/ingestion/jobs.py` |
| Sweeper (beat) | Re-dispatches lost dispatches, expired leases and due retries. Eventual recovery, not an outbox. | `backend/app/ingestion/sweeper.py` |
| Per-owner ingestion cap | Not implemented: a large upload shares the `ingest` queue with everyone (upload requests are rate limited at 30 a minute). | `backend/app/ingestion/jobs.py` has no such limit |
| Per-user chat limits | One active turn per session (a second gets 409), chat 20 requests a minute, upload 30 a minute, a daily cost quota, then 429 with `Retry-After`. | `backend/app/core/ratelimit.py`, `quota.py` |
| Query embedding cache | Redis. A failure degrades to lexical-only search. | `backend/app/retrieval/` |
| Document embedding cache | By content hash and model. A re-upload pays nothing for unchanged chunks. | `backend/app/ingestion/embed_index.py` |
| Paid ingestion LLM outputs | Cached by content hash, prompt version and model. | `llm_cache` table |
| Answers | Never cached. | |

Idempotency, in short: every boundary tolerates a second delivery. Uploads dedupe on `(owner_id, sha256)`. Resource-creating POSTs honour `Idempotency-Key`. A chat message is unique on `(session_id, client_message_id)`, so a retry returns the finished message or the running `run_id`. Each ingestion stage is one fenced transaction that replaces its outputs. Turn persistence is a no-op when the assistant message already exists. The full table is in `ARCHITECTURE.md` section 9.4.

Failure handling: Redis down means uploads return 503 and the limiter fails open; Postgres down means 503; a missing reranker means fusion order with `degraded=no_rerank`; embeddings down means ingestion retries and queries go lexical-only; a turn that dies is finished as `failed` by the reaper.

**How this handles thousands of documents.** Search is exact and scoped to the owner's hash partition, which gives perfect recall and a few milliseconds at tens of thousands of chunks. Ingestion is bound by CPU-heavy extraction, so it scales by adding `worker-ingest` processes; `worker-ai` is I/O-bound and scales with provider rate limits. A document is searchable once `index` commits, before its brief, so a large batch of uploads becomes usable progressively. There is no per-owner cap on concurrently processing versions, so one very large upload can delay others.

---

## 4. Cost

Computed from measured per-document costs and scaled linearly. The brief cost is measured on three documents of different types, and the average is their mean, so it depends on how typical those three are.

Regenerate the brief row: `python3 -c "import json;d=json.load(open('eval/baselines/brief_samples.json'));print(sum(float(v['cost_usd']) for v in d.values())/len(d)*1000)"`

| Item | Per document | Per 1,000 documents | Source |
|---|---:|---:|---|
| Brief, 3 sample documents | $0.0143 (total $0.043026 / 3) | $14.34 | `eval/baselines/brief_samples.json` (`cost_usd` fields 0.015790, 0.024223, 0.003013), command above |
| Embeddings (`text-embedding-3-small`) | TBD (no per-document embedding cost is recorded) | TBD | formula: chunks per document x tokens per chunk x price per token; read `llm_calls` after `make c1-check` |
| Contextual chunk headers (disabled by default) | TBD | TBD | `INDEX_CONTEXT=llm` ingest with `CONTEXTUALIZE_MAX_USD` set |
| **Total, known part only** | $0.0143 (brief only) | **at least $14.34** | arithmetic: the sum of the measured rows |

Assumptions: the three sampled documents (`cuad_consulting`, `fb_ulta_q4`, `auth_travel_policy`) are representative, cost is linear in documents, and cached prompt prefixes and `llm_cache` hits are not credited, so repeated or near-duplicate documents cost less.

Chat cost is per question, not per document.

| Item | Value | Source |
|---|---:|---|
| Answer per turn (analyze plus answer) | $0.0056 on the legacy smoke set; $0.0057 on the golden-set train run | `make eval-smoke`; `make eval-answers-v2 SPLIT=train MAX_USD=3` |
| Smoke evaluation, 21 items | about $0.10 | `make eval-smoke` |
| Full legacy retrieval evaluation, 122 items | about $0.70 | `make eval-retrieval` |
| Planning estimate at 1,000 daily users (133 turns a minute at peak) | about $165 a day at $0.011 a turn; an estimate, not a measurement | arithmetic: 1,000 users x turns per day x cost per turn |

The daily quota (`USER_DAILY_USD`, default $1.00; admins unlimited) sums `llm_calls.cost_usd` since UTC midnight. Ingestion is never blocked by it; the brief stage defers instead. The demo run is capped by `DEMO_MAX_USD` and prints its spend from the ledger.

---

## 5. Growing beyond one machine

A deployment sketch for roughly 1,000 daily users and 100 concurrent turns. It is not built and not load tested.

- **Postgres**: managed, with point-in-time recovery and a read replica for the metrics API. Start at about 8 vCPU and 32 GB (a guess).
- **Connections**: PgBouncer in transaction mode with the asyncpg prepared-statement cache disabled. A `pgbouncer` compose profile exists and is unused in dev.
- **Files**: an S3-compatible object store instead of the local `blobs` volume. Blobs are already addressed by sha256 behind `backend/app/storage/base.py`.
- **Workers**: more `worker-ingest` processes for extraction. Dedicated reranker hosts if the reranker is enabled.
- **Vector search**: switch an owner's partition to an HNSW index with iterative scan once a scope passes about 100,000 chunks. Designed, not built.
- **Recovery**: a transactional outbox instead of sweeper-based recovery.
- **Telemetry**: tail sampling in the collector, and a managed or self-hosted Grafana stack in place of `otel-lgtm`.
- **Growth**: about 4.5 million new chunks a month at 1,000 daily users, which is why a single global index was rejected.
- **First bottlenecks** (estimates): reranker CPU or the database pool; then provider latency variance, which is why time to first token has its own budget.

---

## 6. Observability

- **Two kinds of telemetry.** The ledger (`rag_runs`, `llm_calls`, `job_events`) is permanent product data in Postgres. OpenTelemetry traces, metrics and logs are operational and rolling. They are linked by `trace_id`.
- **Instrumentation** (`backend/app/observability/otel.py`): OTLP export from the api and workers. Spans `vault.node.*` for every turn-graph node and one span per gateway attempt, with IDs and counters only.
- **Collector** (`ops/otel/collector.yaml`, validated by `make otel-validate`): memory limiter, batching, and a redaction processor as a backstop for message content. `make up-full` starts `grafana/otel-lgtm` (collector, Prometheus, Tempo, Loki, Grafana).
- **Logs**: JSON with `trace_id`, `request_id`, `run_id`, `job_id`, `version_id`, route, node, reason and error codes, duration. Never document text, prompts or completions.
- **Metric labels** are bounded enums. IDs go to traces, logs and exemplars.
- **Product APIs**: owner-scoped `GET /v1/metrics/documents`, `/v1/metrics/processing` and `/v1/metrics/chat` (counts, stage durations, failures by error code, turns by outcome, time-to-first-token and latency percentiles, cost per turn), plus the UI page `frontend/src/metrics/MetricsPage.tsx`. `GET /healthz` is liveness; `GET /readyz` reports database, Redis, object store, reranker and provider configuration.
- **Not provided**: Grafana dashboards and alert rules.

---

## 7. Demo script

The demo script, with what to click and say at each step, is [`demo.md`](demo.md). `make demo-record` records the scripted walk-through as video against the running stack (`frontend/e2e/demo.spec.ts`). `scripts/demo.sh` performs the same story with curl and prints the spend; `scripts/demo_core.sh` is a shorter smoke version.
