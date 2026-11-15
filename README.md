# Vault: evidence-first document intelligence

## What it is

Upload PDF, DOCX, TXT or Markdown files. Vault processes them in the background, writes a cited summary (a "source guide") of each, and lets you chat with your documents, one at a time or across all of them.

Every claim in an answer points to the exact sentence it came from. That sentence is copied by code from the stored text, never written by the model, and a separate check marks any segment its citation does not support.

![Chat with a cited answer: each segment carries a citation and a support verdict](docs/img/ui/chat-answer.png)

<table>
<tr>
<td width="50%"><img src="docs/img/ui/chat-empty.png" alt="Empty chat with suggested questions"><br><sub>Empty chat: suggested questions come from the documents.</sub></td>
<td width="50%"><img src="docs/img/ui/chat-conflict.png" alt="Conflict callout showing two sources that disagree"><br><sub>Conflict callout: two documents disagree, both quotes are shown.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/img/ui/library.png" alt="Library with processing status, category and tags"><br><sub>Library: upload, live status, category and tags, and a failed file with a next step.</sub></td>
<td width="50%"><img src="docs/img/ui/chat-answer-mobile.png" alt="The same answer at phone width" width="260"><br><sub>The same answer at phone width.</sub></td>
</tr>
</table>

The screenshots are captured by `frontend/e2e/screenshots.spec.ts` against a mocked API, so the answers are fixtures. Run the stack with a key to see real ones.

## Quickstart

Prerequisites: Docker running, and an OpenAI API key. `make ci` also needs [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env      # then set OPENAI_API_KEY in .env; the key stays in this ignored file
scripts/quickstart.sh     # builds and starts the stack, migrates, prints your API key once
open http://localhost:8100/app
scripts/demo.sh           # the full story with curl: upload, status, guides, streamed chat, why panel, stale marker, metrics
scripts/demo_core.sh      # shorter smoke: upload, ready, one cited answer, history, metrics
```

- Open `/app` (the web UI). On localhost it signs you in automatically as a local user (`LOCAL_AUTOLOGIN=true`, never in prod); on a deployed instance paste an API key. Interactive API docs: `http://localhost:8100/docs`.
- No OpenAI key: `make ui-e2e` runs the UI tests against a scripted fake model on its own ports and tears itself down. `scripts/demo.sh` also runs against that stack with `LLM_PROVIDER=fake` (instructions at the top of the script).
- Docker is only needed to run the stack. Unit tests and lint need none: `make ci`. Docker limits and which target needs what: `docs/operations.md`.

### Local setup without Docker

`backend/uv.lock` is the source of truth for dependencies. `requirements.txt` (runtime) and `requirements-dev.txt` (adds lint, types and tests) are pinned exports of it, for tools that expect pip. Python 3.12 only. Postgres 16 with pgvector and Redis must be running; point `DATABASE_URL` and `REDIS_URL` at them in `.env`.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # or: cd backend && uv sync
cd backend && alembic upgrade head
uvicorn app.main:app --port 8100                       # API
celery -A app.worker.celery_app worker -Q ingest,ai,maintenance -l info   # pipeline worker, second shell
```

Regenerate the two files after changing `pyproject.toml`: `make requirements`.

## The assignment, feature by feature

Source: `docs/spec/case-study.pdf`. Status is one of **built** or **not built**; a behaviour change that did not survive its paired evaluation was deleted, not left switched off (`ARCHITECTURE.md` section 7.3).

### Backend components

| Requirement | Status | Where it lives |
|---|---|---|
| Intelligent upload handler: type and size checks, dedupe, idempotency | Built | `POST /v1/documents`; `backend/app/documents/` (magic-byte sniff, 25 MB streaming cap, dedupe on owner and hash) |
| Smart document analysis: extraction, quality checks, OCR-needed detection | Built | `backend/app/ingestion/extract/`, `quality.py`. Scanned pages are flagged `needs_ocr`; OCR itself is not built |
| Asynchronous processing pipeline | Built | `backend/app/ingestion/`: six leased, idempotent stages on Celery with a sweeper (`ARCHITECTURE.md` section 4) |
| Upload and ingestion API, including new versions | Built | `POST /v1/documents`, `POST /v1/documents/{id}/versions`, `GET /v1/versions/{id}/processing` (and `/processing/stream`) |
| Document retrieval API | Built | `GET /v1/documents`, `GET /v1/documents/{id}`, `DELETE /v1/documents/{id}`, `GET /v1/chunks/{id}`, `GET /v1/versions/{id}/file` |
| AI insights API: summary, tags, category, key facts | Built | `GET /v1/documents/{id}/brief`; `backend/app/insights/`. Every fact is quote-verified by code |
| Start chat session | Built | `POST /v1/chat/sessions` |
| Ask a question, with source citations | Built | `POST /v1/chat/sessions/{id}/messages`; `backend/app/chat/` |
| Get chat history | Built | `GET /v1/chat/sessions/{id}/messages` (segments, evidence, follow-ups, stale markers) |
| Multi-turn context | Built | `backend/app/chat/memory.py`: the recent turns, with the question rewritten standalone |
| Document statistics API | Built | `GET /v1/metrics/documents` |
| Processing metrics API | Built | `GET /v1/metrics/processing`, plus `GET /v1/metrics/chat` for turns, latency, cost |
| Data storage: metadata, insights, chats, citations, metrics, performance | Built | `backend/app/db/models/` (`ARCHITECTURE.md` section 3); the cost and run ledger is `llm_calls` and `rag_runs` |

### Technical stack

| Requirement | Status | Where it lives |
|---|---|---|
| Python, FastAPI, PostgreSQL, Celery with Redis | Built | `backend/`, `docker-compose.yml` |
| File processing | Built | pdfplumber and python-docx in `backend/app/ingestion/extract/` |
| OpenAI API: generation and embeddings | Built | Responses API and `text-embedding-3-small`, through one gateway (`backend/app/llm/gateway.py`) |
| Local or S3-compatible storage | Built | Content-addressed local store behind an interface (`backend/app/storage/`) |
| RAG framework (LangChain or LlamaIndex) | Not used, by choice | A typed turn graph of our own, so routing is testable and the model never picks control flow (`backend/app/chat/graph.py`) |
| pytest, API documentation | Built | `backend/tests/`; OpenAPI at `/docs`; Postman collection `docs/api/vault.postman_collection.json` |

### Bonus features

| Feature | Status | Where it lives |
|---|---|---|
| Multi-document chat | Built | `POST /v1/chat/sessions` with `document_ids` for a chosen set, or omitted for all documents; every source is cited |
| Intelligent follow-up suggestions | Built | Returned with each answer and rendered as chips (`backend/app/chat/followups.py`) |
| Summary customization (length, focus, tone) | Built | `POST /v1/documents/{id}/summaries` |
| Automated categorization and tagging | Built | Part of the brief (`category`, `tags`) |
| Key insight extraction | Built | Key facts in `GET /v1/documents/{id}/brief`, each cited and verified in `backend/app/insights/verify.py` |
| Sentiment analysis | Built, per document | The brief classifies each document's tone (`positive`, `negative`, `mixed`, `adversarial`, `formal`, `neutral`) with a one-line reason and up to three cited sentences (`backend/prompts/brief_extract@3.md`); per-passage sentiment is not built |
| Comparative analysis between documents | Built | Compare route with per-document retrieval and a conflict callout (`backend/app/chat/compare.py`, `conflicts.py`). |
| Simple frontend | Built | `/app`: library, document page with source viewer, streaming chat, metrics (`frontend/`) |
| Real-time updates | Built, over SSE | `GET /v1/versions/{id}/processing/stream` for processing, `text/event-stream` for chat; no WebSocket |
| Smart caching | Built | Redis caches query embeddings (`backend/app/retrieval/`); chunk embeddings are cached by content hash (`backend/app/ingestion/embed_index.py`); paid ingestion outputs go through `llm_cache`. Answers are deliberately never cached |
| Vector database | Built | pgvector in PostgreSQL: exact search in an owner's hash partition (`backend/app/retrieval/vector.py`) |
| Streaming chat responses | Built | SSE with status stages, per-segment events and time to first token (`backend/app/chat/stream.py`, `events.py`) |
| Comprehensive test coverage | Built | Unit, integration, security and UI end-to-end suites (`make ci`, `make test`, `make ui-e2e`) |
| Cost tracking | Built | One `llm_calls` row per model attempt; cost per turn in `GET /v1/metrics/chat` |
| Rate limiting and quota | Built | Redis token buckets per user and a daily cost quota with 429 and `Retry-After` |
| Document version tracking | Built | `POST /v1/documents/{id}/versions`; answers that cited an old version are marked stale with a one-click re-ask (`backend/app/insights/staleness.py`) |
| Batch processing optimizations | Built | Parallel embedding batches and parallel brief map calls per document (`backend/app/ingestion/embed_index.py`, `backend/app/insights/stage.py`) |
| Detailed logging and observability | Built | JSON logs, OpenTelemetry traces, optional Grafana stack (`make up-full`); `docs/operations.md` section 6 |
| Health check and monitoring endpoints | Built | `GET /healthz`, `GET /readyz`, the metrics APIs |

### Beyond the spec

| Feature | Status | Where it lives |
|---|---|---|
| "Why this answer" panel from reason codes and templates | Built | `GET /v1/runs/{id}`; `backend/app/explain/` |
| Support check per cited segment | Built | `backend/app/chat/support_check.py`; verdicts shown next to each segment |
| Meta questions about your library, answered by owner-scoped SQL | Built | `backend/app/chat/meta.py` |
| Whole-document answering for small scopes | Built, on request only | `backend/app/chat/full_context.py`; it is not automatic, because it showed no gain for twice the cost |
| Accessibility checks on the UI | Built | Automated axe scans and a keyboard-flow test in `frontend/e2e/a11y.spec.ts`; there has been no manual screen-reader audit |

### Deliverables

| Deliverable | Status | Where it lives |
|---|---|---|
| Working backend | Built | `backend/` |
| README with setup, architecture, assumptions, scale | Built | this file, `ARCHITECTURE.md`, `docs/operations.md` |
| AI usage log and prompt engineering | Built | `AI_USAGE.md`; the prompts are `backend/prompts/<id>@<version>.md` |
| Demo of document chat | Script built, recording not included | `scripts/demo.sh`, `scripts/demo_core.sh`, the narration script `docs/demo.md`, and `make demo-record`, which records the walk-through as video |

## API quick tour

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

**6. Metrics** (counts, latency and time-to-first-token percentiles, cost per turn, feedback)

```bash
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/metrics/documents
curl -fsS -H "Authorization: Bearer $KEY" $API/v1/metrics/chat
```

The Postman collection is `docs/api/vault.postman_collection.json`.

## How it works

An API (FastAPI) accepts uploads and chat and does no CPU-heavy work. Celery workers extract, chunk, embed and summarize. PostgreSQL with pgvector is the single source of truth (metadata, vectors, full-text, the turn ledger). Redis carries queues, rate-limit buckets and caches. OpenAI models are called only through one gateway. Retrieved passages are reranked by one listwise call to the `fast` model. Observability is an optional sidecar the system survives losing.

A chat turn is an explicit graph of nodes: load context, analyze (or skip on the fast path), retrieve and rerank, generate a structured answer whose citations are evidence handles, verify each cited segment, persist the whole turn in one transaction. Code, not the model, chooses every edge. The diagram is generated from `GRAPH.describe()` and a test fails when it is stale.

| Decision | Why | Where |
|---|---|---|
| Citations are application-managed: sentences get handles (`E3.1`), the model returns handles in a strict schema, code copies the stored sentence | A model cannot invent a quote; an unknown handle is dropped | `backend/app/chat/citations.py` |
| Support check per cited segment | Correct-looking citations can still fail to support a claim | `backend/app/chat/support_check.py` |
| No LangChain: our own typed turn graph | Routing is testable, the diagram comes from code, the model never picks control flow | `backend/app/chat/graph.py` |
| OpenAI only, addressed by role (`fast`, `balanced`, `deep`, `flagship`) | Model names live in one routing file and settings | `backend/app/llm/routing.py` |
| API-key auth, with a session cookie for the UI | The smallest thing that isolates users; every repository call takes an owner id and cross-user access returns 404 | `backend/app/core/auth.py` |
| Explanations come from reason codes and templates | The model is never asked to explain itself | `backend/app/explain/` |
| Exact vector search inside the owner's hash partition | Perfect recall and a few milliseconds at owner scale | `backend/app/retrieval/vector.py` |

Assumptions: the reviewer runs on one machine with Docker; one owner has thousands of documents, not millions; answers are never cached. The complete design, with every decision and its evidence, is in `ARCHITECTURE.md`.

## Quality, scale and cost at a glance

The shipped configuration: retrieval `retrieval@5` (an LLM listwise rerank of 50 fused candidates, top 8 kept), prompts `analyze_query@4`, `answer_rag@12`, `answer_compare@3` and the advisory `support_check@1`. Judges: `eval_judge_claims@5`, `eval_judge_support@3`, `eval_judge_abstain@1` (the frozen runs were judged with `@4`; the `@5` re-judge of dev gives the same answer pass, `docs/evaluation.md` section 2). The judges are not calibrated against human labels and share a vendor with the generator, so every judged number is provisional. Two identical runs differ by about 2 points of answer pass and 1 to 3 points of faithfulness. Method, baselines and the full tables are in `docs/evaluation.md`.

**Sealed test split.** Run once, on the frozen configuration (code `1c92d64`, run `01a1db51-9481-7106-8c9c-64e1a87bacd6`), with nothing tuned on it before or after. The test gold is a **draft**: its 134 items were never audited, so these numbers are measured against unreviewed labels.

| Measure (test split, 134 items, draft gold) | Value | Command |
|---|---|---|
| Answer pass; claim completeness | 0.761 (102 of 134); 0.779 | `make eval-answers SPLIT=test MAX_USD=4` |
| Faithfulness (support rate of cited segments); abstention correct | 0.957; 0.913 | `make eval-answers SPLIT=test MAX_USD=4` |
| Citation integrity; prompt-injection violations; `must_not_contain` | 1.00; 0 of 11; 14 of 14 pass | `make eval-answers SPLIT=test MAX_USD=4` |
| Retrieval recall@8 | 0.753 | `make eval-answers SPLIT=test MAX_USD=4` |
| Cost and latency per turn; whole run including judges | $0.007, 7.3 s mean; $3.41 | `make eval-answers SPLIT=test MAX_USD=4` |

**Train and dev.** The baseline is the earlier default (fusion order without a reranker, `retrieval@2`, and an earlier answer prompt), re-judged with the same judges and gold as the final runs. Three numbers are kept apart: the locked benchmark (original gold, never edited), the audited-gold score, and the engineering gain (gold and judges held fixed). A gold correction raises both columns and is a benchmark correction, not an engineering gain.

| Measure | Baseline | Final | Command |
|---|---|---|---|
| Answer pass, train, audited gold, 118 items (run `01a1db3b`) | 0.763 (90) | 0.873 (103) | `make eval-answers SPLIT=train MAX_USD=3` |
| Answer pass, dev, current gold (disputed items reviewed), 59 items (run `01a1db44`) | 0.746 (44) | 0.847 (50) | `make eval-answers SPLIT=dev MAX_USD=3` |
| Locked benchmark: answer pass, train, original pre-audit gold, 123 items | 0.699 (86) | 0.772 (95) | `cd backend && PYTHONPATH=.. uv run python -m eval.rejudge --answers ANSWERS --max-usd 1` on a checkout of the pre-audit gold (`docs/evaluation.md` section 2) |
| Faithfulness, train / dev | 0.853 / 0.888 | 0.899 / 0.906 | `make eval-answers SPLIT=dev MAX_USD=3` |
| Citation integrity (no invented citation reaches the user) | 1.00 | 1.00 | `make eval-gate-t0` |
| Brief cost per document, mean of 3 samples | | $0.0143 | `python3 -c "import json;d=json.load(open('eval/baselines/brief_samples.json'));print(sum(float(v['cost_usd']) for v in d.values())/len(d))"` |
| Corpus documents reaching ready | | 19 of 19 in 36 s | `make c1-check` |

Engineering gain with gold and judges fixed: +11.0 points on train (13 items net) and +10.2 on dev (6 items net), from `python -m eval.paired BASELINE.json FINAL.json --metric answer_pass --ledger` (`docs/evaluation.md` section 1.3). Against the targets fixed before measuring (answer pass 0.80, faithfulness 0.90, citation chunk precision 0.85), the test run meets faithfulness and misses answer pass (0.761) and chunk precision (0.658). Scale: search is exact and scoped to the owner's hash partition, ingestion is bound by CPU-bound extraction and grows by adding `worker-ingest` processes, and no load test has been run (`docs/operations.md`).

## Testing

| Suite | Command |
|---|---|
| Lint, types, unit, frontend checks (no Docker) | `make ci` |
| Reviewer path from a fresh clone (add `--stack` for the stack and smoke) | `scripts/fresh_clone_check.sh` |
| Unit, integration, end to end (needs `make up`) | `make test` |
| Integration only (Postgres and Redis only) | `make up-test` then `make test-integration` |
| Corpus ingestion | `make c1-check` |
| UI end to end with a fake model | `make ui-e2e` |
| Live provider smoke (spends a few cents) | `make smoke-providers` |

Live and evaluation commands always run on samples with a spend cap, never on the full dataset by default.

## Limits

- Scanned PDFs end as `needs_ocr` (OCR is not built), and PDFs over 600 pages are unsupported.
- Gold status differs by split: train gold is audited (an independent model audit, then human decisions), dev gold was reviewed where items were disputed, and test gold is an unaudited draft. The judges are not calibrated against human labels, and judge and generator share a vendor.
- No concurrency or throughput claim exists beyond single-document measurements; no load test has been run.
- Observability with Grafana is off by default (`make up-full`).
- The full list is in `ARCHITECTURE.md` section 12 ("Limits and gaps"), and what is not yet measured is in `docs/evaluation.md` section 6.

## Repo map

| Path | Purpose |
|---|---|
| `backend/app/` | FastAPI app: api, ingestion, retrieval, chat, insights, feedback, llm gateway, explain, metrics, observability |
| `backend/prompts/` | Versioned prompt files (`<id>@<version>.md`) |
| `backend/config/` | Versioned retrieval configuration |
| `backend/tests/` | Unit, integration and security tests |
| `frontend/` | React UI served at `/app`, with Playwright specs in `frontend/e2e/` |
| `eval/` | Golden sets, graders, judges, harness, frozen baselines (`eval/baselines/`) |
| `ops/` | OpenTelemetry collector configuration |
| `scripts/` | Quickstart, demo, diagram and Postman generators (see `scripts/README.md`) |
| `docker-compose.yml` | Default stack; `docker-compose.ui-test.yml` is the fake-model UI test stack |
| `Makefile` | All commands (`make up`, `make ci`, `make eval-*`) |
| `AI_USAGE.md` | How AI tools were used, with corrections and the prompt log |

## Documentation

| Document | What it covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System design: components, data model, pipelines, models, explainability, decisions and limits |
| [`docs/evaluation.md`](docs/evaluation.md) | How quality is measured, current numbers, decisions from measurements, the runbook |
| [`docs/operations.md`](docs/operations.md) | Dev environment, fresh-clone check, scale and cost, observability |
| [`docs/demo.md`](docs/demo.md) | The 3 to 4 minute demo script: what to click and say |
| [`docs/voice.md`](docs/voice.md) | How Vault talks |
| [`CLAUDE.md`](CLAUDE.md) | Rules and conventions for contributors and coding agents |
