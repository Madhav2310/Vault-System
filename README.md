# Vault: evidence-first document intelligence

## What it is

Upload PDF, DOCX, TXT or Markdown files. Vault processes them in the background, writes a cited summary (a "source guide") of each, and lets you chat with your documents. Every claim in an answer points to the exact sentence it came from, and that sentence is copied by code from the stored text, never written by the model.

Screenshot or GIF of a cited streaming answer: pending (recorded by the author, see `docs/ops/demo.md`). Screen recording: pending (link added by the author).

## Quickstart

```bash
scripts/quickstart.sh     # needs Docker running and OPENAI_API_KEY in .env; prints your API key once
open http://localhost:8100/app
scripts/demo.sh           # full story with curl: upload, status, guides, streamed chat, why panel, stale marker, metrics
scripts/demo_core.sh      # shorter smoke: upload, ready, one cited answer, history, metrics
```

- Open `/app` (the web UI) and paste the API key `quickstart.sh` printed. API docs: `http://localhost:8100/docs`.
- No OpenAI key: `make ui-e2e` runs the UI tests against a scripted fake model on its own ports and tears itself down. `scripts/demo.sh` also runs against that stack with `LLM_PROVIDER=fake` (instructions at the top of the script).
- Docker is only needed to run the stack. Unit tests and lint need none: `make ci`. Docker limits and which target needs what: `docs/ops/dev-environment.md`.
- The recorded walkthrough and its narration script: `docs/ops/demo.md`.

## What works today

- [x] Upload with async processing, dedupe, new versions, delete (`make c1-check` runs the 19-document corpus)
- [x] PDF, DOCX, TXT, MD extraction with exact character positions
- [x] Hybrid retrieval (vectors plus full-text), reranking, and cited chat with multi-turn memory
- [x] Citations copied by code from stored sentences; unknown handles are dropped
- [x] Streaming answers (server-sent events) with status stages, time to first token, and suggested follow-ups
- [x] Cited source guide per document, and custom summaries
- [x] Whole-document answering for small scopes, an evidence gate before answering, and a per-segment support check
- [x] Meta questions about your library ("how many documents do I have?") answered by owner-scoped SQL
- [x] Stale-answer marker when a newer document version arrives, and re-ask
- [x] "Why this answer" panel from reason codes and templates (`GET /v1/runs/{id}`)
- [x] Metrics APIs (documents, processing, chat), daily cost quota, rate limits, answer feedback
- [x] Web UI at `/app`: library, document page with source viewer, streaming chat with hover and click citations
- [x] Compare-across-documents route and conflict detection (`backend/app/chat/compare.py`; per-document quotas behind `CHAT_COMPARE_QUOTA` pending its A/B)
- [ ] Accessibility pass on the UI (planned: S19.06)
- [ ] Answer-quality baseline with judges (pending: `make eval-answers`)
- [ ] Recorded demo (pending, author)

## Feature tour

Set up once. `KEY` is the API key printed by `scripts/quickstart.sh`.

```bash
API=http://localhost:8100
KEY=paste-your-key-here
```

**1. Upload** (returns 202 and starts processing; re-uploading the same bytes returns the existing version)

```bash
curl -fsS -H "Authorization: Bearer $KEY" -F "file=@frontend/e2e/fixtures/services-agreement.txt" $API/v1/documents
curl -fsS -H "Authorization: Bearer $KEY" "$API/v1/documents?limit=10"
```

**2. Live status and source guide** (the version id comes from the upload response)

```bash
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/versions/VERSION_ID/processing
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/documents/DOCUMENT_ID/brief
```

**3. Chat, streamed** (omit the `Accept` header for a single JSON answer; event types are served at `/v1/chat/events/schema`)

```bash
SID=$(curl -fsS -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{}' $API/v1/chat/sessions | jq -r .id)
curl -fsSN -H "Authorization: Bearer $KEY" -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"content": "What is the notice period for termination?", "client_message_id": "q1"}' \
  $API/v1/chat/sessions/$SID/messages
```

**4. Follow-up and compare** (same session; a question over several documents cites each source, and a document with a newer version marks old answers stale)

```bash
curl -fsS -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"content": "And which law governs it?", "client_message_id": "q2"}' $API/v1/chat/sessions/$SID/messages
curl -fsS -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"content": "Compare the monthly fee in the services agreement and the pricing memo.", "client_message_id": "q3"}' \
  $API/v1/chat/sessions/$SID/messages
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/chat/sessions/$SID/messages
```

**5. Why panel** (`run_id` comes from the `run.started` event or the message)

```bash
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/runs/RUN_ID
```

**6. Metrics** (counts, latency and TTFT percentiles, cost per turn, feedback)

```bash
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/metrics/documents
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/metrics/chat
```

A Postman collection is in `docs/api/vault.postman_collection.json`.

## Architecture

Full architecture, models, explainability and decisions: `ARCHITECTURE.md` (diagram sources in `docs/img/*.mmd`; `make diagrams` renders the SVGs). The original design specification is `docs/design.md`.

- **Topology.** An API (FastAPI) accepts uploads and chat and does no CPU-heavy work. Celery workers extract, chunk, embed and summarize. PostgreSQL with pgvector is the single source of truth (metadata, vectors, full-text, the turn ledger). Redis carries queues, rate-limit buckets and caches. OpenAI models are called only through one gateway. The reranker and observability are optional sidecars the system survives losing.
- **Ingestion.** An upload is sniffed, deduplicated by owner and hash, stored by content hash, and recorded with a job row in one transaction. Six stages (extract, quality, chunk, embed, index, brief) each claim the job with a lease and commit once, so redelivery is harmless. Permanent errors end immediately; retryable ones back off and then fail with a retry action. A failed brief never blocks chat.
- **Chat turn.** An explicit graph of nodes: load context, analyze (or skip on the fast path), retrieve, gate the evidence, generate a structured answer whose citations are evidence handles, verify each cited segment, persist the whole turn in one transaction. Code, not the model, chooses every edge. The diagram is generated from `GRAPH.describe()` and a test fails when it is stale.

## Key design decisions and assumptions

| Decision | Why | Evidence |
|---|---|---|
| Citations are application-managed: sentences get handles (`E3.1`), the model returns handles in a strict schema, code copies the stored sentence | A model cannot invent a quote; an unknown handle is dropped | `backend/app/chat/citations.py`; citation integrity in the Evaluation table |
| Whole-document answering for small scopes, retrieval otherwise | Full context beats retrieval on small scopes in the literature (external numbers, not ours) | `backend/app/chat/full_context.py`; our own A/B is pending (`make eval-answers`) |
| Evidence gate before answering | Abstain instead of answering from weak evidence | `backend/app/chat/gate.py`, `backend/config/retrieval@2.yaml` |
| Support check per cited segment | Correct-looking citations can still fail to support a claim; segments are marked cited or unsupported | `backend/prompts/support_check@1.md`, `backend/app/chat/support_check.py` |
| No LangChain: our own typed turn graph | Routing is testable, the diagram comes from code, the model never picks control flow | `backend/app/chat/graph.py`, `backend/tests/unit/docs/test_diagrams.py` |
| OpenAI only, by role (`fast`, `balanced`, `deep`, `flagship`) | Model names live in one routing file and settings; the Anthropic adapter is dormant | `backend/app/llm/routing.py` |
| API-key auth for the demo (`Authorization: Bearer`) | Smallest thing that isolates users; every repository call takes an owner id and cross-user access returns 404 | `backend/app/core/auth.py` |
| Explanations come from reason codes and templates | The model is never asked to explain itself | `backend/app/explain/views.py` |

Assumptions: the reviewer runs on one machine with Docker; one owner has thousands of documents, not millions; answers are never cached.

## Evaluation

Golden set: `eval/datasets/vault_eval_v1`, with gold labels still marked draft (every number below is pre-review). The retrieval rows below were measured while the reranker timed out in most runs, so they mostly measure plain fusion; the re-baseline with the fixed reranker is pending.

| Measure | Value | Command |
|---|---|---|
| Golden set size | 122 items | `make eval-retrieval` |
| Retrieval recall@8 and mrr@40, full run | 0.759 and 0.505 | `make eval-retrieval` |
| Retrieval recall@8 target, at least 0.85 | not met | `make eval-retrieval` |
| Retrieval recall@8 and mrr@40, fast run (100 items with gold, retrieval@1) | 0.744 and 0.508 | `make eval-retrieval-fast` |
| Recall@8 by question type: lookup, identifier, conflict, compare, table | 0.82, 0.67, 0.58, 0.55, 0.40 | `make eval-retrieval` |
| Per-quote recall@8, fusion only versus as served | 0.639 versus 0.654 | `cd backend && PYTHONPATH=.. uv run --frozen python -m eval.replay --run latest --rank rrf --by-category` |
| Citation integrity (no invented citations), smoke run | 1.00 | `cd backend && PYTHONPATH=.. uv run --frozen python -m eval.run --suite smoke --config baseline_smoke --pipeline eval.pipeline_http:setup` |
| Smoke run, 21 items: recall@8, cited-chunk precision, abstain recall | 0.81, 0.74, 1.0 | `cd backend && PYTHONPATH=.. uv run --frozen python -m eval.run --suite smoke --config baseline_smoke --pipeline eval.pipeline_http:setup` |
| Abstention precision (smoke), target at least 0.80 | 0.50, not met | `make eval-smoke` |
| Answer latency p50, before streaming and the fast path | 5.9 s, target 3.5 s not met | `make eval-smoke` |
| Brief facts verified, 3 sample documents | 56 of 58 | `python3 -c "import json;d=json.load(open('docs/evidence/brief_samples.json'));f=[x for v in d.values() for x in v['facts']];print(sum(x['status']=='verified' for x in f),len(f))"` |
| Gold quotes locatable in extracted PDFs | 100.0 percent | `make eval-extraction` |
| Corpus documents reaching ready | 19 of 19 in 36 s | `make c1-check` |
| Retrieval recall@8 with the working reranker (retrieval@2) | pending | `make up-rerank` then `make eval-retrieval-fast RETR_CONFIG=retrieval@2` |
| Answer pass, faithfulness, citation precision, completeness with CIs | pending | `make eval-answers SPLIT=train MAX_USD=3` |
| Whole-document versus retrieval answer quality (paired) | pending | `make eval-answers SPLIT=train MAX_USD=3 FILTER=scope_docs=1` |

Judges are OpenAI models, like the generator; this is mitigated (deterministic graders first, reference-guided judging) but not eliminated. Paired statistics for every A/B live in `eval/paired.py`.

## Scale

How the design handles thousands of documents, what was measured and what was not: `docs/ops/scale.md`. In short: search is exact and scoped to the owner's hash partition, ingestion is bounded by CPU-bound extraction on the `ingest` worker and scales by adding workers, and no load test was run. Measured rows and TBD rows each name their source or the command that would measure them.

## Cost

Per-document and per-turn costs, scaled in `docs/ops/scale.md`. The demo run is capped by `DEMO_MAX_USD` and prints its spend from the ledger.

| Item | Value | Command |
|---|---|---|
| Brief per document, mean of 3 samples | $0.0143 | `python3 -c "import json;d=json.load(open('docs/evidence/brief_samples.json'));print(sum(float(v['cost_usd']) for v in d.values())/len(d))"` |
| Brief for 1,000 documents, derived | $14.34 | `python3 -c "import json;d=json.load(open('docs/evidence/brief_samples.json'));print(sum(float(v['cost_usd']) for v in d.values())/len(d)*1000)"` |
| Answer per turn (analyze plus answer), before the fast path | $0.0056 | `make eval-smoke` |
| Smoke eval, 21 items | about $0.10 | `make eval-smoke` |
| Full retrieval eval, 122 items | about $0.70 | `make eval-retrieval` |
| Retrieval-only fast eval | about $0.0001 | `make eval-retrieval-fast` |
| Embeddings per document (no per-document embedding cost is recorded yet) | pending | `scripts/demo.sh` |
| Demo run spend, target below $0.20 | pending | `scripts/demo.sh` |

## Testing

| Suite | Command | Count |
|---|---|---|
| Lint, types, unit, frontend checks (no Docker) | `make ci` | 330 passed |
| Unit, integration, e2e (needs `make up`) | `make test` | 669 passed, 1 flaky (passes alone) |
| Integration only (Postgres and Redis only) | `make up-test` then `make test-integration` | included in the line above |
| Corpus ingestion | `make c1-check` | 19 passed |
| UI end to end with a fake model | `make ui-e2e` | pending |
| Live provider smoke (spends a few cents) | `make smoke-providers` | pending |

Counts come from `docs/status/progress.md` (S17.12 and S16 gate entries) and age as the code changes; rerun the command for the current value. Live and eval commands always run on samples with a spend cap, never on the full dataset by default.

## Limitations and deliberately not built

- Cut from plan v3 as low visibility for their cost (`docs/plan/v3.md` section 1): convergence-engine tuning stages, a comparison matrix view, load tests, Grafana dashboards, geometry vision QA of highlights.
- Not finished: the audited v2 answer-quality baseline and the A/B decisions on the new switches (see `docs/status/TOTAL_PROGRESS.md`), and OCR for scanned PDFs (they end as `needs_ocr`).
- Known limits: gold labels are unreviewed drafts; judges and generator share a vendor; no concurrency or throughput claim beyond the single-document measurements in `docs/ops/scale.md`; PDFs over the page cap (600 pages) end as unsupported.
- Optional heavy components, off by default: reranker (`make up-rerank`; retrieval falls back to fusion order without it), observability with Grafana (`make up-full`), the web UI build (`make ui`, built automatically in the Docker image).

## Repo map

Architecture overview: [`ARCHITECTURE.md`](ARCHITECTURE.md). Documentation index: [`docs/README.md`](docs/README.md).

| Path | Purpose |
|---|---|
| `backend/app/` | FastAPI app: api, ingestion, retrieval, chat, insights, matrix, feedback, llm gateway, explain, observability |
| `backend/prompts/` | Versioned prompt files (`<id>@<version>.md`) |
| `backend/tests/` | Unit and integration tests |
| `backend/config/` | Backend configuration files |
| `frontend/` | React UI served at `/app`, with Playwright e2e specs in `frontend/e2e/` |
| `reranker/` | Optional cross-encoder rerank service (`make up-rerank`) |
| `eval/` | Golden sets, graders, judges, harness, frozen baselines (`eval/baselines/`) |
| `ops/` | OpenTelemetry collector and Grafana configuration |
| `scripts/` | Quickstart, demo, plan tooling, spikes (see `scripts/README.md`) |
| `docs/design.md` | Design, source of truth |
| `docs/plan/` | Plan of record (slices, acceptance criteria); parsed by `scripts/plan_lint.py` |
| `docs/status/` | Status, total progress, progress log, followups, spec coverage |
| `docs/eval/` | Eval gate rules and the v2 runbook |
| `docs/ops/` | Scale, dev environment, demo script |
| `docs/research/` | Research 01-06, plus `reports/` and `notes/` from the rebuild research |
| `docs/evidence/` | Spikes, A/B write-ups, kept eval runs (`runs/`) |
| `docs/spec/`, `docs/api/`, `docs/img/` | Case-study PDF, Postman collection, diagram sources |
| `docker-compose.yml` | Default stack; `docker-compose.ui-test.yml` is the fake-model UI e2e stack |
| `Makefile` | All commands (`make up`, `make ci`, `make eval-*`) |
| `AI_USAGE.md` | AI usage log |
