# backend/app: package map

FastAPI + Celery application. Design source of truth: `docs/design.md`.

| Package | Purpose |
|---|---|
| `api/` | HTTP layer: routers, response schemas, errors (problem+json), idempotency, middleware, OpenAPI |
| `core/` | Settings, auth, rate limit, daily quota, UUIDv7 ids, logging, error-code catalog |
| `db/` | Async engine/session, repository base classes, models, Alembic `migrations/`, dev `seed` |
| `documents/` | Upload, content-addressed blobs, magic-byte sniffing, document/version creation, delete/purge |
| `storage/` | Object store contract (`base`) and local filesystem implementation |
| `ingestion/` | Stage pipeline: extract, chunk, contextualize, embed/index; job claim/lease, sweeper, status machine |
| `insights/` | Document brief (extract, verify, staleness), custom summaries |
| `retrieval/` | Scope, vector + lexical search, fusion, rerank, expansion; `retrieve()` facade |
| `chat/` | Turn graph and nodes, evidence handles, citations, gate/abstain, SSE stream, sessions, ledger |
| `llm/` | Gateway (the only path to a model), routing, pricing, prompt registry, provider adapters |
| `explain/` | Reason-code templates and the user view of a run |
| `feedback/` | Answer ratings |
| `metrics/` | Read-side statistics for documents, processing and chat |
| `observability/` | OpenTelemetry setup |
| `worker/` | Celery app, sync-to-async runtime, tasks (ingest, summaries, purge, maintenance) |

Entry points: `main.py` (ASGI app), `worker/celery_app.py` (Celery).

Invariants (see `CLAUDE.md` for the full list): every model call goes through `llm.gateway`; only `llm/routing.py` and
settings name models; repository methods take `owner_id`; no DB connection is held across an LLM call or stream;
document text never enters logs or spans; delivery is at-least-once, so consumers are idempotent.
