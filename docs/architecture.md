# Architecture

Three diagrams: what runs where, how a document becomes searchable, and what happens in one chat turn. Sources are Mermaid files in `docs/img/*.mmd`; the rendered SVGs are `docs/img/*.svg` (`make diagrams`, which needs Node via `npx`). The authoritative design is `docs/design.md`; section numbers below point into it.

## 1. Topology

![Topology](img/topology.svg) ([source](img/topology.mmd))

Legend:
- Solid arrow: a call or data flow on the default stack. Dashed arrow: an optional dependency that the system survives losing.
- Cylinder: a store. Rounded box: an external service. Box with a `profile` label: not started by `make up`.
- The `docker-compose.yml` service names are `api`, `worker-ingest`, `worker-ai`, `beat`, `postgres`, `redis`, `reranker` (profile `rerank`), `otel-lgtm` (profile `obs`).

The API is stateless: it owns HTTP, authentication, scope computation and turn orchestration, and does no CPU-heavy work on the event loop (design §3, §13.1). All asynchronous work runs in Celery workers, but job state lives in Postgres (`processing_jobs`, leases and compare-and-set claims), never only in Celery. Postgres is the single source of truth (metadata, vectors, full-text, the turn ledger). Redis holds the broker, rate-limit buckets and caches, so losing it degrades service but loses no data. The reranker is a separate CPU-bound service so it can fail independently (a failure becomes `degraded=no_rerank` and retrieval falls back to fusion order). Observability is a sidecar: losing it never affects requests. The only external dependency is OpenAI, and every model call goes through `app.llm.gateway`.

## 2. Ingestion

![Ingestion](img/ingestion.svg) ([source](img/ingestion.mmd))

Legend:
- Top: the six stages in order (`STAGE_ORDER` in `backend/app/ingestion/runner.py`), with the Celery queue each runs on (`ingest` is CPU-bound, `ai` is I/O-bound).
- Bottom: the document-version status machine (design §6.2). Dotted arrows show which stage moves the version to which status.
- Terminal states: `needs_ocr`, `unsupported` and `failed` are reached from `processing`; `superseded` is reached when a newer version of the same document becomes ready.

An upload streams to a temp file, is sniffed by magic bytes, deduplicated by `(owner, sha256)`, stored content-addressed, and recorded with a `processing_jobs` row in one transaction (design §6.1). Each stage claims the job with a lease, does its work, and commits once, fenced by the lease, so a worker that lost its lease commits nothing and redelivery is harmless. Permanent errors (corrupt, encrypted, unsupported) end immediately; retryable ones back off with jitter up to five attempts, then the version is `failed` with a retry action. A sweeper on `beat` re-dispatches stuck jobs. A failed `brief` leaves the version at `ready`, so chat never waits on it.

## 3. Chat turn

![Chat turn](img/chat_turn.svg) ([source](img/chat_turn.mmd))

This diagram is **generated from code**: `scripts/gen_turn_diagram.py` writes `docs/img/chat_turn.mmd` from `GRAPH.describe()` in `backend/app/chat/turn.py`, and `backend/tests/unit/docs/test_diagrams.py::test_turn_nodes_match_steps` fails when the committed file is stale.

Legend:
- Rectangle: a node (an async function over the typed turn state).
- Diamond: a router, named after the function that picks the next node in code. Its outgoing arrows are the targets it declares.
- Circles: start and end.
- Not drawn: any node that raises or times out jumps to `persist` (the graph's `on_error` node), which writes an error response, so a turn never ends stuck. A turn executes at most 12 nodes.

The code, not the model, chooses every edge; the model returns typed decisions that routers apply (design §8.2). A turn loads the session and scope, analyzes the question into a standalone query (or asks a clarifying question), retrieves, gates the evidence, generates a structured answer whose citations are evidence handles, verifies the cited segments, and persists the whole turn to the ledger in one transaction. Citation text is copied by code from the stored sentences, never taken from the model. Some routers are still single-target today (for example `after_route_question`, `after_gate`); they are the places where planned branches attach, so the diagram shows the current routing, not the target design.
