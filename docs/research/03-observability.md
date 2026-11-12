# Pass 3 — Observability stack and dashboards

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.

Tags: **[V]** verified this session · **[C]** believed, verify at build · **[U]** measure.

---

## 1. Two kinds of telemetry, deliberately separate

| | Product telemetry (the evidence ledger) | Operational telemetry (OTel) |
|---|---|---|
| Question it answers | *Why did this answer say this?* *What did this cost?* | *Is the system healthy? Where is time going?* |
| Store | Postgres (`rag_runs`, `run_steps`, `retrieval_candidates`, `llm_calls`, `job_events`, `feedback`) | Prometheus (metrics), Tempo (traces), Loki (logs) |
| Retention | Durable, part of the product | Rolling (e.g. 7–30 days) |
| Consumers | Users, explain UI, spec's metrics API, eval | Engineers, alerts |
| Cardinality | Unbounded is fine (per run, per chunk) | Labels must be bounded |

They're linked by IDs: every `rag_runs` row stores `trace_id`, every log line carries `trace_id` + `run_id`, and the operator view has a "View trace" deep link into Grafana. **Don't duplicate:** cost and quality dashboards read the ledger directly (Grafana Postgres datasource, read-only role) instead of re-emitting the same numbers as metrics.

---

## 2. Instrumentation: OpenTelemetry everywhere

Why OTel: vendor-neutral. The same instrumentation can export to Grafana, Langfuse, Datadog or Honeycomb by changing the collector config, not the code. The GenAI semantic conventions (`gen_ai.*`) are still marked **Development** and moved to a separate repo in 2026 [C], so we use them for LLM spans but keep our own `vault.*` namespace for everything domain-specific.

| Layer | Instrumentation |
|---|---|
| FastAPI / ASGI | `opentelemetry-instrumentation-fastapi` [C] |
| DB | SQLAlchemy / asyncpg instrumentors [C] |
| Redis | redis instrumentor [C] |
| Celery | `opentelemetry-instrumentation-celery`: injects W3C trace context into task headers and extracts it in the worker; **must be active on both producer and worker**; with prefork, initialize in `worker_process_init` [C] |
| Anthropic | **Native SDK OpenTelemetry (beta)**: `Anthropic(open_telemetry={...})`, spans `anthropic.messages.create` with `gen_ai.usage.*` incl. cache read/write [V: skill docs]. Content mode default `metadata_only` — **we keep it**, because documents must not leave into telemetry. Pin the SDK version (beta attribute names can change). Note `gen_ai.usage.input_tokens` on spans *includes* cache tokens, unlike the API response [V]. |
| OpenAI (embeddings, judge) | `opentelemetry-instrumentation-openai-v2` [C]; message-content capture off |
| httpx (reranker service calls) | httpx instrumentor |
| Our code | One span per harness node (`vault.node.analyze_query`, …) carrying `vault.run_id`, `vault.route`, `vault.mode`, `vault.reason_code`, `vault.degraded`; one span per ingestion stage (`vault.stage.extract`, …) with `vault.version_id`, page/chunk counts |

Optional server-side **Claude API tracing** (an org admin adds an OTLP destination; spans emitted by the API itself) needs a public endpoint, so it's not for local dev. Note it as a production option [V: skill docs].

**Trace shape of one chat turn:**
```
POST /v1/chat/sessions/{id}/messages                        (fastapi)
 └ vault.turn  run_id=…, route=compare, mode=rag
   ├ vault.node.guard            └ db spans
   ├ vault.node.load_memory      └ db spans
   ├ vault.node.analyze_query    └ anthropic.messages.create (haiku)
   ├ vault.node.retrieve         ├ openai.embeddings (or cache hit event)  └ db: hybrid query
   ├ vault.node.rerank           └ http → reranker service └ vault.rerank.infer
   ├ vault.node.evidence_gate    └ anthropic.messages.create (haiku)  [or event gate_skipped]
   ├ vault.node.generate         └ anthropic.messages.create (sonnet, stream) events: first_token
   └ vault.node.persist          └ db spans
 └ celery.apply_async followups  ──►  (worker) vault.task.followups └ anthropic…
```
**Upload trace:** `POST /v1/documents` → `celery.apply_async` → worker `vault.stage.extract` → `chunk` → `embed` (openai spans) → `index` → `brief`, all one trace across processes.

---

## 3. Backend stack

| Option | Fit | Verdict |
|---|---|---|
| **`grafana/otel-lgtm`**: one container = OTel Collector + Prometheus + Tempo + Loki + Grafana (+ Pyroscope) [C]. OTLP on 4317/4318, Grafana on 3000. **Grafana says dev/demo only** [C] | Perfect for compose and the demo | **Chosen for dev/demo** |
| Same components deployed separately (or Grafana Cloud) | Production path | Documented, no code changes |
| SigNoz / OpenObserve | Good all-in-one OTel backends | Fine alternatives; no advantage here |
| **Langfuse v3** (MIT): web + worker + Postgres + **ClickHouse** + Redis + S3 [C] | Excellent LLM trace UI, prompt management, evals | **Not self-hosted here.** Too much infrastructure for what our ledger already provides. Because we emit OTel, pointing the collector at Langfuse later is a config change. |
| **Arize Phoenix**: single container, OTel/OpenInference, license **ELv2** (source-available) [C] | Light LLM trace explorer | Optional dev profile only (`docker compose --profile llmobs`) |
| Sentry | Exceptions with stack context | Optional; errors are already visible as error spans + logs |

**OTel Collector config (our own file, mounted in):**
- receivers: otlp
- processors:
  - `memory_limiter`, `batch`
  - **redaction**: drop any `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` attributes. Defense in depth even though the SDKs are in metadata-only mode.
  - **tail_sampling** (off in dev): keep 100% of traces with errors, latency above SLO, abstentions, degraded runs or negative feedback; keep 10–20% of the rest at scale
  - `resource` attributes: `service.name` (`vault-api`, `vault-worker-ingest`, `vault-worker-ai`, `vault-reranker`), `deployment.environment`, `service.version` (git SHA)
- exporters: Prometheus remote-write / OTLP → Tempo, Loki

**Logs:** structlog JSON to stdout with `trace_id`/`span_id` injected (OTel logging integration), shipped via OTLP. Fields: `event, level, request_id, run_id, job_id, version_id, user_id, route, node, reason_code, error_code, duration_ms`. **Never** document text, prompts or completions (CLAUDE.md rule). A unit test scans captured logs from an end-to-end run for chunk text and fails if it finds any.

**Profiling:** Pyroscope (bundled) on API, workers and reranker. Useful for the CPU hot paths (extraction, reranking, tokenization) [U: overhead].

**Metric cardinality rules:** labels only from bounded enums: `route, node, mode, model, purpose, lane, provider, status, error_code, reason_code, stage, queue`. `run_id/user_id/doc_id` **never** become labels; they live in traces and logs and are attached as **exemplars** on latency histograms, so a slow point on a chart links to its trace.

---

## 4. Metrics catalog

### LLM (OTel GenAI-aligned names where they exist)
| Metric | Type | Labels |
|---|---|---|
| `gen_ai.client.operation.duration` | histogram | model, purpose, status |
| `gen_ai.client.token.usage` | histogram | model, purpose, token_type (input/output/cache_read/cache_write) |
| `vault.llm.cost_usd` | counter | model, purpose, lane |
| `vault.llm.ttft_seconds` | histogram | model, purpose |
| `vault.llm.errors` | counter | model, error_type (rate_limit, overloaded, timeout, refusal, max_tokens, bad_request) |
| `vault.llm.limiter_wait_seconds` | histogram | model, lane |
| `vault.llm.circuit_state` | gauge | model |

### Chat / RAG
| Metric | Type | Labels |
|---|---|---|
| `vault.turn.duration_seconds`, `vault.turn.ttft_seconds` | histogram | route, mode |
| `vault.turn.outcome` | counter | route, outcome (answer, abstain, clarify, error), coverage_state |
| `vault.node.duration_seconds` | histogram | node, status |
| `vault.gate.decision` | counter | decision (skip_high, llm, abstain_low), result |
| `vault.retrieval.candidates` | histogram | source (vector, lexical) — catches the filtered-HNSW recall bug |
| `vault.retrieval.retry` | counter | outcome |
| `vault.degraded` | counter | kind (lexical_only, no_rerank) |
| `vault.citations.per_answer` | histogram | mode |
| `vault.citations.unresolved` | counter | — **invariant: must stay 0** |
| `vault.highlight.level` | counter | level (sentence, chunk, page) |
| `vault.conflicts.detected` | counter | — |
| `vault.sse.active_streams` | gauge | — |
| `vault.session.conflict` (409s) | counter | — |

### Ingestion
| Metric | Type | Labels |
|---|---|---|
| `vault.ingest.time_to_ready_seconds` | histogram | mime, size_bucket |
| `vault.stage.duration_seconds` | histogram | stage, status |
| `vault.jobs.by_state` | gauge (from DB by beat) | state, stage |
| `vault.jobs.oldest_pending_age_seconds` | gauge | queue |
| `vault.jobs.lease_expired`, `vault.jobs.redispatched` | counter | stage |
| `vault.ingest.failures` | counter | error_code |
| `vault.ingest.pages_needing_ocr` | counter | — |
| `vault.embed.cache_hits` | counter | — |

### Platform
HTTP RED from FastAPI instrumentation; DB pool in-use/wait; event-loop lag (custom probe); Celery queue length (Redis list length, sampled by beat); reranker inflight/queue; Postgres via `postgres_exporter` (connections, locks, slow queries, index hit rate) [C]; Redis memory/evictions; container CPU/memory.

---

## 5. SLOs and alerts

| SLO | Target [U, set after baseline] | Alert |
|---|---|---|
| Turn success: answer, abstain or clarify (not error) | 99% over 30 days | Multi-window burn rate (fast 1h / slow 6h) |
| RAG TTFT p95 | ≤ 3 s | p95 > 3 s for 10 min |
| Full-context TTFT p95 | ≤ 8 s | p95 > 8 s for 10 min |
| Time-to-READY p95 (≤ 50-page PDF) | ≤ 60 s | Oldest pending job age > 5 min |
| Citation integrity | 100% resolved | **Any** `citations.unresolved` > 0 → page |
| Cost | ≤ budget/day | Cost/hour > 2× trailing average |

**Quality-drift alerts** (they catch the "model update broke grounding" failure NotebookLM users reported):
- Abstention rate, gate-skip rate or citations/answer shifts > 3σ vs a 7-day baseline.
- Negative-feedback rate above threshold.
- Scheduled nightly eval (`make eval-smoke`, ~15 questions) scores below the last baseline. Results are written to `eval_results` and charted.

Operational alerts: 429 rate > 5% for 5 min · circuit open · reranker p95 > 500 ms · DB pool wait p95 > 50 ms · lease expirations spike · Redis down / limiter fail-open.

---

## 6. Dashboards (provisioned as JSON in `ops/grafana/dashboards/`)

**D1 — Service overview** (the page you open first)
Row 1 stats: turns/min · turn success % (SLO gauge + error budget left) · RAG TTFT p95 · active SSE streams · cost today.
Row 2: request rate and errors by route · latency p50/p95/p99 with exemplars → traces.
Row 3 saturation: DB pool in-use/wait · event-loop lag · reranker queue · Celery queue length · limiter wait by lane.

**D2 — RAG pipeline**
- Stacked per-node latency (analyze, retrieve, rerank, gate, generate) by route: shows where a slow turn spends its time.
- Route mix and mode mix (RAG vs full-context) over time.
- Gate decisions (skip / LLM / abstain) and retry outcomes.
- Coverage distribution (supported / partial / insufficient), conflicts detected.
- Degraded runs by kind · candidates returned (vector vs lexical) · citations per answer · highlight precision level.
- Table: slowest 20 runs in the window (from ledger) with "open run" / "open trace" links.

**D3 — LLM & cost** (ledger-backed where possible)
- Tokens by model × purpose (input / output / cache read / cache write) · cache hit ratio (`cache_read / input`).
- Cost per hour by purpose · cost per answered question (p50/p95) by route and mode.
- Errors by type (429, 529, timeout, refusal) · circuit state · limiter wait by lane.
- Top users by spend (ledger, table).

**D4 — Ingestion**
- Uploads/hour · time-to-READY distribution · stage duration heatmap.
- Jobs by state · oldest pending age per queue · lease expirations and re-dispatches (sweeper activity).
- Failures by error_code · pages needing OCR · extraction quality score distribution · embedding cache hit rate.

**D5 — Quality**
- Nightly eval scores over time per config (recall@k, faithfulness, citation precision, abstention accuracy) with annotations for prompt/model version changes.
- Online proxies: abstention rate, negative-feedback rate, "wrong citation" flags, uncited segments.
- Table: newest flagged answers → run link → eval inbox status.

**D6 — Infrastructure**
Postgres (connections, locks, slow queries, cache hit ratio, table and index sizes incl. the HNSW index) · Redis (memory, evictions, ops) · containers (CPU, memory, restarts).

**In-app, not Grafana:** the spec's `/v1/metrics/documents` and `/v1/metrics/processing` are user-facing, per-user and ledger-backed. The operator view of a single run (waterfall + candidates) is in the product UI and links to Tempo for the infrastructure-level trace.

---

## 7. Eval as observability

- `eval_runs(id, git_sha, config jsonb, started_at)` and `eval_results(run_id, question_id, metric, value)` are written by `make eval`.
- Grafana D5 charts them, and each prompt/model version bump is drawn as an annotation. A quality regression is then as visible as a latency regression.
- Production → eval loop: flagged feedback goes to `eval/inbox/`, is reviewed, then promoted into `golden.yaml`.

---

## 8. Compose additions

```
otel-lgtm      grafana/otel-lgtm (pinned tag), ports 3000, 4317, 4318
               mounts: ops/otel/collector.yaml, ops/grafana/dashboards/, datasource for Postgres (read-only role)
postgres-exporter (optional profile)
phoenix        (optional profile: llmobs)
```
All services get `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4317`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=dev,service.version=$GIT_SHA`.
Tests run with an in-memory span exporter to assert trace shape (e.g. the Celery span shares the upload's trace ID; every node span carries `vault.run_id`).

---

## Sources
- Anthropic SDK OpenTelemetry reference (claude-api skill, `shared/opentelemetry.md`, `python/claude-api/opentelemetry.md`)
- [OTel GenAI semconv not stable yet (DEV)](https://dev.to/azena-ai/opentelemetrys-genai-semantic-conventions-are-not-stable-yet-heres-what-actually-shipped-in-2026-3mke)
- [Dash0 — GenAI semantic conventions explained](https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained)
- [Greptime — OTel GenAI semantic conventions](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)
- [Langfuse vs Arize Phoenix (Langfuse)](https://langfuse.com/compare/arize-phoenix)
- [Langfuse vs Phoenix self-hosted (AgenticWire)](https://www.agenticwire.news/article/langfuse-vs-arize-phoenix)
- [Grafana — introducing grafana/otel-lgtm](https://grafana.com/blog/2024/03/13/an-opentelemetry-backend-in-a-docker-image-introducing-grafana-otel-lgtm)
- [OTel Celery instrumentation docs](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/celery/celery.html)
- [OneUptime — propagate trace context through Celery headers](https://oneuptime.com/blog/post/2026-02-06-propagate-opentelemetry-trace-context-celery-headers/view)
- [DEV — tracing FastAPI and Celery in one trace](https://dev.to/temitopeajao/tracing-async-python-how-to-instrument-fastapi-and-celery-in-the-same-trace-8jo)
