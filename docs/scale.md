# Scale: how would this handle thousands of documents?

Short answer: the design handles thousands of documents per owner on one Postgres without changes, because search is exact and scoped to the owner's partition. Ingestion throughput is bounded by CPU-bound extraction on the `ingest` worker and scales by adding workers. What is measured and what is not is spelled out below.

Rules for this page:
- Every number has a source (a file, or a command that regenerates it) in the last column. A row whose number was not measured says `TBD` and names what would measure it. Derived numbers say how they are derived.
- **No load test was run** (cut from plan v3, `docs/plan/v3.md` section 1). Nothing here is a concurrency or throughput claim beyond the single-document measurements listed.
- Measurements come from a 16 GB M4 laptop, mostly native, some in capped containers.

## 1. Ingestion: time and memory per document

| Measure | Value | Source |
|---|---|---|
| PDF extraction, AMD 10-K (121 pages, 420,251 chars), before the S16.05 fix | 37 s, 720 MB peak | `docs/progress.md` ("S16.05 extraction time and memory"); regenerate: profile the AMD 10-K extraction as in that entry (not a make target) |
| Same document after the fix | 11.3 s, 198 MB peak | `docs/progress.md` ("S16.05 extraction time and memory") |
| Extraction seconds per page, after the fix | 0.093 s/page (derived: 11.3 s / 121 pages) | `docs/progress.md` ("S16.05 extraction time and memory") |
| 19-document corpus to `ready` | 36 s (was 113 s before S16.05) | `make c1-check`, recorded in `docs/progress.md` ("S16 Docker gate") |
| Page cap | 600 pages, then `unsupported/too_many_pages` | `docs/progress.md` ("S16.05 extraction time and memory") |
| Embed batches in flight per document | 4 (`embed_concurrency`); 8 batches of 0.5 s finish in 2.0 s or less against 4 s or more sequential | `docs/progress.md` ("S16.08 ingestion concurrency"), test `backend/tests/integration/ingestion/test_ingest_concurrency.py` |
| Brief map calls in flight per document | 3 (`brief_concurrency`) | `docs/progress.md` ("S16.08 ingestion concurrency") |
| AMD 10-K time to `ready` (searchable), sequential vs parallel embedding (S16.08) | 28.8 s vs 20.2 s (-30%); time to `enriched` (brief done) 57.6 s vs 57.4 s (one brief call, so map concurrency does not apply) | 2026-11-14, `make up-rerank`, upload `eval/sources/fb_pdfs/AMD_2022_10K.pdf` with `EMBED_CONCURRENCY=1 BRIEF_CONCURRENCY=1` vs defaults (4/3), poll `GET /v1/documents/{id}` |
| Ingestion stage time, 45 real documents (where time goes) | brief 16.7 s p50 / 52 s p95 (80% of stage time, runs after the document is searchable); embed 1.7 / 5.4 s; extract 0.7 / 13.8 s; chunk, quality, index < 0.03 s | `job_events` table: `select stage, percentile_cont(0.5) within group (order by duration_ms) from job_events group by stage` |
| Documents per hour per `worker-ingest` | TBD (not measured; needs a corpus run on `make up`) | none yet |

Derived estimate, extraction only: one ingest worker at the measured 0.093 s/page needs about 9,300 s (2.6 h) for 1,000 documents of 100 pages each, ignoring embed and brief (which run on the separate `ai` queue and overlap). This is arithmetic on the row above, not a measurement; `worker-ingest` runs one process (`-c 1`), so two workers halve it.

## 2. Chunks per document

| Measure | Value | Source |
|---|---|---|
| Chunks, AMD 10-K (59 of them tables) | 494 | `docs/progress.md` ("S1.22 Chunker `chunker@1`") |
| Chunk counts across the 15-document corpus | 1 to 429 | `docs/progress.md` ("C1 checkpoint run"), `make c1-check` |
| Corpus median chunks per document | TBD (run `make c1-check` and query `select count(*) from chunks group by version_id`) | none yet |
| Chunk median tokens | 356 on the narrative excerpt, 166 over the whole AMD filing | `docs/progress.md` ("S1.22 Chunker `chunker@1`") |

The two AMD figures differ (494 versus a 429 maximum) because they come from different runs and extraction versions; the corpus run predates S16.05. Re-measure before quoting either as current.

## 3. Search at scale

| Measure | Value | Source |
|---|---|---|
| Exact top-40 vector query on a 21k-chunk owner | 8.9 ms, pruned to 1 of 32 hash partitions | `docs/evidence/spike_schema.md`, `scripts/spikes/run_schema_spike.sh explain` |
| Scoped vector search, 1,500 rows, plan plus execution | 6.5 ms plan, 23.6 ms end to end | `docs/progress.md` ("C2 lanes merged", S2.02/S2.03) |
| HNSW switch point | 100,000 chunks in one scope (a design default, not a measured crossover) | `docs/design.md` section 7.3 |
| Exact search latency at 100,000 chunks in one scope | TBD (measure with the S0.20 spike script at that size) | none yet |
| Reranker, 40 passages, native, 4 threads, batch 8 | p50 260 ms, 354 MB peak | `docs/progress.md` ("S16.02 reranker configuration"); `python bench.py --threads 4 --batch 8 --words 180 --n 40` in `reranker/` |
| Reranker in its container (3 CPUs, 3 threads, 20 passages) | p50 350.8 ms, p95 578 ms, 230 MB | `docs/progress.md` ("S16.02 AC6 container bench") |
| Retrieval-only eval wall time, 100 items, warm | 145 s, about $0.0001 | `docs/evidence/retrieval_ab.md`, `make eval-retrieval-fast` |

Why exact search first: an owner's corpus is thousands to tens of thousands of chunks, exact search has perfect recall, and a global HNSW index would cost RAM and lose recall under owner filters (`docs/design.md` decision D4, `docs/research/04-scale-and-idempotency.md`). Chunks are hash-partitioned by owner so a scoped query touches one partition. Only the 1-owner cases above were measured; a many-owner dataset was not.

## 4. Queueing, fairness and caches

Design facts (`docs/design.md` sections 6.6, 13.4, 7.4). Behavior under load is **not measured**.

| Mechanism | What it does | Source |
|---|---|---|
| Job claim with lease and heartbeat | Duplicate delivery collapses; a worker that loses its lease commits nothing | `docs/design.md` section 6.6 |
| Per-owner cap on processing versions | 5, so a 500-file upload does not starve other users | `docs/design.md` section 6.6 |
| Per-user chat limits | at most 3 concurrent turns, 20 turns/min, daily quota, then 429 with `Retry-After` | `docs/design.md` section 13.4 |
| Background versus interactive lanes in the gateway | Background yields to interactive demand below a 30% reserve floor | `docs/design.md` section 13.2 |
| Query embedding cache | Redis, 7 days | `docs/design.md` section 7.4 |
| Document embedding cache | by `content_hash` and model, permanent; a re-upload pays nothing for unchanged chunks | `docs/design.md` section 7.4, `docs/progress.md` ("S1.24 Embed stage") |
| Paid ingestion LLM outputs | cached by content hash, prompt version and model | `docs/design.md` section 7.4 |
| Answers | never cached | `CLAUDE.md` hard rules |

## 5. Cost per 1,000 documents

Computed from measured per-document costs and scaled linearly. The brief cost is measured on three documents of different types; the per-document average is their mean, so it depends on how typical those three are.

Regenerate the brief row: `python3 -c "import json;d=json.load(open('docs/evidence/brief_samples.json'));print(sum(float(v['cost_usd']) for v in d.values())/len(d)*1000)"`

| Item | Per document | Per 1,000 documents | Source |
|---|---:|---:|---|
| Brief, 3 sample documents | $0.0143 (total $0.043026 / 3) | $14.34 | `docs/evidence/brief_samples.json` (`cost_usd` fields: 0.015790, 0.024223, 0.003013), command above |
| Embeddings (`text-embedding-3-small`) | TBD (no per-document embedding cost is recorded; the live probe logged `cost=$0` for 3 tokens, and the price is flagged unconfirmed) | TBD | `docs/progress.md` ("S0.23 OpenAI Responses provider": `text-embedding-3-small in=3 cost=$0`); formula: chunks per document x tokens per chunk x price per token |
| Contextual chunk headers | TBD (S17.05 not run; budgeted at about $0.30 for the eval corpus) | TBD | `docs/plan/v3.md` section 9 |
| **Total, known part only** | $0.0143 (brief only) | **at least $14.34** | sum of the measured rows, `docs/evidence/brief_samples.json` |

Assumptions: the three sampled documents (`cuad_consulting`, `fb_ulta_q4`, `auth_travel_policy` in `docs/evidence/brief_samples.json`) are representative; cost is linear in documents; cached prompt prefixes and `llm_cache` hits are not credited, so repeated or near-duplicate documents cost less. Cost per chat turn is separate and per question, not per document:

| Item | Value | Source |
|---|---:|---|
| Answer cost per turn (analyze plus answer) | $0.0056 | `docs/STATUS.md` section 5; regenerate with `make eval-smoke` |
| Design estimate at 1,000 DAU (133 turns/min peak) | about $165/day at $0.011/turn (an estimate, tagged unconfirmed in the design) | `docs/design.md` section 13.3 |

## 6. What changes at 100x

From `docs/design.md` section 20.2 (a deployment sketch, not something that was built or load tested):

- Postgres: managed, 8 vCPU and 32 GB as a starting guess (marked unconfirmed in the design), point-in-time recovery, and a read replica for dashboards and the metrics API.
- Connections: PgBouncer in transaction mode with the asyncpg prepared-statement cache disabled; a `pgbouncer` compose profile exists for this and is not used in dev.
- Files: object store (S3) instead of the local `blobs` volume; the code already addresses blobs by sha256.
- Reranker: separate CPU-optimized hosts, two or more; the first bottleneck named in the design is reranker CPU or the DB pool (`docs/design.md` section 13.3).
- Workers: more `worker-ingest` processes for extraction, which is the CPU-bound step; `worker-ai` is I/O-bound and scales on provider rate limits.
- Vector search: switch an owner's partition to HNSW with iterative scan once a scope passes the switch point in section 3. This path is designed but the switch has not been built or measured.
- Observability: a managed or self-hosted Grafana stack instead of `otel-lgtm`.
- Growth: about 4.5M new chunks per month at the design's 1,000 DAU assumption (`docs/design.md` section 13.3), which is why a single global index was rejected.
