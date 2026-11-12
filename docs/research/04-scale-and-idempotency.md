# Pass 4 — Scale (1000 DAU / 100 concurrent), idempotency, load testing

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.

Every number below is an **estimate from stated assumptions** [U] unless tagged. Prices are [V] from the Claude API skill table (cached 2026-10-06); the OpenAI embedding price is [C]. The point is the *formulas and the bottleneck ordering*; the load test (§6) replaces the guesses with measurements.

---

## 1. Workload assumptions

| Parameter | Value | Note |
|---|---|---|
| DAU | 1000 | |
| Turns per user per day | 15 | |
| Uploads per user per day | 3 docs × 20 pages avg | |
| Peak concurrent active users | 100 | |
| Think time between turns at peak | ~45 s | → **~2.2 turns/s ≈ 133 turns/min at peak** |
| Daily volume | 15k turns · 3k docs · 60k pages | |
| Corpus growth | 90k docs/month · ~50 chunks/doc → **~4.5M chunks/month** | Changes the vector-index decision (§3) |
| Mode mix | 85% RAG, 15% full-context | |
| Gate | LLM gate on ~50% of RAG turns (tiered) | |

## 2. LLM load: the first bottleneck

Per RAG turn (Sonnet 5.5 answers, Haiku 5.5 elsewhere):

| Call | Input tokens | Output tokens |
|---|---|---|
| analyze_query (Haiku) | ~1.5k | ~150 |
| evidence_gate (Haiku, 50%) | ~4k | ~300 |
| generate (Sonnet) | ~7k (1.5k system cached + 1k history + ~4.5k evidence) | ~400 |
| follow-ups (Haiku, async) | ~1.5k | ~100 |
| full-context turns (15%) | ~40k evidence, mostly cache reads after the first turn | ~600 |

**Peak per-minute load (133 turns/min):**
- Sonnet input ≈ 133 × 7k ≈ **0.93M ITPM**, plus full-context ≈ 20 × 40k ≈ **0.8M** (cache reads; whether these count toward ITPM on our tier is [U])
- Sonnet output ≈ 133 × 400 ≈ **53k OTPM**
- Haiku input ≈ 133 × (1.5k + 2k + 1.5k) ≈ **0.67M ITPM**

**Whether this fits depends entirely on the account's rate-limit tier [U]. It is the first thing to check, not the last.** Levers, in order: prompt caching (system prompt + full-context evidence) → evidence budget tuning (fewer, better chunks after rerank) → gate skipping → queueing with honest "waiting for capacity" status → a higher tier.

**Cost per turn (RAG, Sonnet):**
- generate ≈ 7k × $2/M + 400 × $10/M ≈ $0.014 + $0.004, minus the cached system prompt ≈ **$0.016**
- Haiku calls ≈ 5k × $0.10/M + 550 × $0.50/M ≈ **$0.0008**
- full-context amortized ≈ 15% × (40k × $0.20/M cache read) ≈ **+$0.0012**
- **≈ $0.018/turn → 15k turns ≈ $270/day (~$8k/month).** On Opus 5.5 the generate part roughly doubles (≈ $0.035/turn).

**Ingestion LLM cost/day:** 60k pages ≈ 30M tokens
- embeddings at ~$0.02/M [C] ≈ $0.60
- brief extraction on Haiku ≈ 30M × $0.10/M ≈ $3
- contextual prefixes (if enabled) are mostly Haiku cache reads, a few $ [U]

**Conclusion:** chat generation dominates cost (~98%). Optimizing ingestion cost is irrelevant; optimizing evidence tokens per turn is the lever that matters.

## 3. Database: the corpus-size trap, and the fix

At ~4.5M chunks/month, one **global HNSW index** over 1536-dim float32 vectors is ~27 GB of raw vectors after a month, plus the graph. That's over RAM, and owner-filtered queries make HNSW worse: selectivity per owner is tiny, so iterative scans have to walk far.

**Key insight: every query is scoped to one owner's documents.** Average user corpus ≈ 90 docs × 50 chunks ≈ 4.5k chunks after a month (power users maybe 50k). **Exact (brute-force) distance over 5k–50k vectors is fast [U: expect single-digit to tens of ms] with perfect recall.** So:

| Decision | Detail |
|---|---|
| Denormalize `owner_id` onto `chunks` | So scope filtering doesn't need joins |
| **Hash-partition `chunks` by `owner_id`** (e.g. 32 partitions) | Partition pruning; smaller per-partition indexes; vacuum and reindex in pieces |
| B-tree on `(owner_id, version_id)` | Scope selection |
| **Exact search within scope by default** | `ORDER BY embedding <=> :q LIMIT 40` over the scoped rows |
| `halfvec(1536)` storage [C: pgvector supports halfvec] | Halves memory with negligible recall loss [U, verify in eval] |
| HNSW only as an escape hatch | Per partition, used when a scope exceeds a threshold (e.g. > 100k chunks), with iterative scan |
| Lexical: GIN on tsvector with `owner_id` via `btree_gin` composite, or rely on partition pruning [C] | Avoids scanning every owner's postings for common words |

This **replaces pass 2 §A6** (HNSW-first). Pass 2's iterative-scan advice still applies to the escape hatch.

Other DB load at peak: ~15–25 short queries per turn × 2.2 turns/s ≈ 50 qps, plus ingestion writes. Trivial for one Postgres (8 vCPU / 32 GB [U]). Connection budget: API 3 replicas × 2 procs × pool 10 = 60 + workers ~30 + Grafana/exporters ~10 → ~100 connections. Use **PgBouncer** (transaction mode) once replicas grow. With asyncpg + transaction pooling, **disable prepared statement caching** (or use a compatible setting) [C].

Blob storage: ~90 GB/month at ~1 MB/doc → S3-compatible in production. The local volume is dev-only.

## 4. Compute services

| Service | Load at peak | Sizing [U] | Note |
|---|---|---|---|
| API | 133 turns/min, ~100 open SSE streams, I/O-bound | 2–3 replicas × 2 uvicorn workers | **Any synchronous CPU work (token counting, JSON on big payloads, PDF) must leave the event loop.** Event-loop lag is a dashboard metric. |
| **Reranker** | 133 turns/min × 40 pairs (+ retries) ≈ **90–100 pairs/s** | MiniLM-L6: likely 1–2 cores; bge-reranker-base: likely 3–6 cores | The second bottleneck. Separate service, bounded queue, timeout → fall back to RRF order (`degraded=no_rerank`). The model choice is a cost/latency/quality trade the eval decides. |
| worker-ingest | 60k pages/day ≈ 8 CPU-hours/day at ~0.5 s/page [U] | 2 replicas × 4 procs handles 5× bursts | Time-to-READY is dominated by queue wait during bursts; watch oldest-job age. |
| worker-ai | embeddings, briefs, follow-ups, compaction | 2 replicas, concurrency 8–16 | Throttled by the background lane |
| Redis | broker + buckets + caches | Single small instance | Not a bottleneck |

**Bottleneck order at 100 concurrent:**
1. LLM rate limits (ITPM)
2. Reranker CPU
3. Vector search *if* done with a global HNSW (fixed by §3)
4. Ingestion queue age during upload bursts
5. DB connections (fixed by PgBouncer)

The web tier is never the bottleneck.

## 5. Backpressure and fairness

- **Per-user limits:** at most 1 active turn per session (CAS) and 3 per user, 20 turns/min, a daily token quota. Return `429` with `Retry-After` and quota headers.
- **Interactive lane empty:** the turn waits up to ~5 s, emitting SSE `status{waiting_for_capacity}`, then returns `503 capacity` with `retry-after`. It never queues silently for a minute.
- **Background lane** pauses whenever interactive demand rises (it borrows only above the reserve floor). Matrix fan-out runs in this lane with a per-matrix concurrency cap, so one huge matrix can't starve others.
- **Ingestion fairness:** per-owner cap on concurrently processing documents (e.g. 5), so one user's 500-file folder upload doesn't block everyone's single file. Jobs are claimed round-robin across owners (the claim query orders by per-owner in-flight count, then age).

## 6. Idempotency at every boundary

Principle: delivery is **at-least-once everywhere** (HTTP retries, Celery late ack, sweeper re-dispatch). Each consumer is idempotent, so the effects are **effectively-once**.

| Boundary | Mechanism |
|---|---|
| `POST /documents` | Optional `Idempotency-Key` header → `idempotency_keys(owner_id, key, request_hash, response, created_at)`, 24 h TTL; same key + different body → `422`. Natural dedupe: `(owner_id, sha256)` returns the existing version. |
| Blob write | Content-addressed key `blobs/{sha256}`; rewriting is a no-op |
| Job dispatch (API and sweeper) | `UPDATE processing_jobs SET last_dispatched_at=now() WHERE id=:id AND (last_dispatched_at IS NULL OR last_dispatched_at < :cutoff) RETURNING id`. Only the winner enqueues, so two sweepers can't double-dispatch. |
| Task execution | Task payload is only `job_id`. The worker **claims** with CAS on the lease; if a live lease exists, the task is a no-op. Duplicate deliveries collapse. |
| Stage outputs | One transaction: delete this version's outputs for the stage, insert the new ones, advance `current_stage`. Unique keys: `(version_id, ordinal)` for chunks, `(chunk_id, embedding_model)` for embeddings. |
| Paid LLM work in ingestion | Cached by `(content_hash, prompt_id@version, model)`, so re-runs after a crash don't pay twice |
| `POST /chat/.../messages` | **Client-generated `client_message_id`**, unique `(session_id, client_message_id)`. A retry returns the existing message/run: if done, the stored result; if running, the `run_id` to poll. This also makes SSE reconnect safe. |
| Session run ownership | CAS with lease; release fenced by `run_id` |
| Follow-ups / memory compaction tasks | Keyed by `message_id` / `session_id + upto_message_id`; upsert |
| Staleness marking | `UPDATE … SET stale_since=now() WHERE … AND stale_since IS NULL` |
| Matrix cells | Unique `(matrix_id, version_id, column_id, column_version)`; re-run creates a new column version |
| Summaries | Unique `(version_id, params_hash, prompt_version)` |
| Feedback | Upsert on `(user_id, run_id, segment_id, kind)` |
| Deletion | Soft delete (excluded from retrieval immediately), async purge keyed by version; blob deleted when its refcount reaches 0 |
| Beat schedule | Single beat instance (`--max-replicas 1` or a Redis lock), and every scheduled task is idempotent anyway |

LLM calls themselves are not idempotent (each attempt costs money). They're bounded by retry budgets, and each attempt gets its own `llm_calls` row, so cost accounting stays honest.

## 7. Load and chaos test plan

**Tooling:** Locust (Python, SSE-capable with a custom client) [C], or k6. Scenarios are in `loadtest/`.

**Traffic mix:** 70% chat turns (consume SSE to `done`) · 15% browse/list/brief · 10% uploads (real PDFs from the corpus) · 5% matrix creation.

**Two provider modes:**
1. **FakeProvider with realistic latency** (TTFT ~0.8 s, ~40 tokens/s, gate ~0.5 s, 1% injected 429s). This tests *our* system at 100+ concurrent without spending money or hitting tier limits.
2. **Real providers, small:** 10 concurrent for 5 min. Measures true TTFT, cache hit ratio and rate-limit headroom, then extrapolates against §2.

**Data seeding for DB scale:** a synthetic corpus of 1000 owners × 90 docs × 50 chunks ≈ 4.5M chunk rows with random halfvecs. This validates owner-scoped exact search latency and partition pruning (`EXPLAIN ANALYZE` captured in the report).

**Phases:** ramp 10 → 100 → 150 concurrent (find the knee) · 30 min soak at 100 · upload burst (500 files from one user) during chat load.

**Chaos (each with an expected outcome):**
- kill a worker mid-stage → lease expiry → recovered
- restart Redis → uploads 503 briefly; chat continues
- reranker down → `degraded=no_rerank`, no errors
- 429 storm → limiter backs off, interactive prioritized
- Postgres failover/restart → 503 then recovery, no duplicate jobs
- client disconnects mid-stream → message still persisted

**Pass criteria (FakeProvider mode, 100 concurrent):**
- our overhead on TTFT p95 < 500 ms (TTFT minus fake provider TTFT)
- zero 5xx outside chaos windows
- DB pool wait p95 < 50 ms
- event-loop lag p99 < 100 ms
- scoped vector query p95 < 50 ms at 4.5M rows
- oldest ingestion job age < 5 min during a 3× burst
- no duplicate chunks, runs or messages after chaos (verified by SQL invariant checks)

Results go in `docs/loadtest-report.md` with the exact commands that reproduce them.

## 8. Deployment topology at 1000 DAU

Still one region and one Postgres. No Kubernetes required (ECS / Fly / Render / two VMs all work):
```
LB (SSE-friendly timeouts ≥ 120 s)
 ├─ api ×3                 (stateless)
 ├─ reranker ×2            (CPU-optimized)
 ├─ worker-ingest ×2 (4 procs) · worker-ai ×2 · beat ×1
 ├─ PgBouncer → Postgres (managed, 8 vCPU / 32 GB, PITR backups; read replica for Grafana and metrics API)
 ├─ Redis (managed, small)
 ├─ S3 (blobs)
 └─ OTel collector → Grafana stack (managed, or self-hosted separately — not otel-lgtm)
```

**What changes beyond ~10k DAU** (documented, not built):
- transactional outbox
- moving vectors to a dedicated store only if per-owner scopes grow past exact-search limits
- a GPU reranker or a hosted rerank API
- per-tenant rate limits
- multi-region read paths
