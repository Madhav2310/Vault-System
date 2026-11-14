# Vault: evidence-first document intelligence

Upload documents, get cited summaries, chat across them; every claim cites a stored sentence. Docs: `README.md` (run it), `ARCHITECTURE.md` (design and rationale; its last table says what to read before changing each area), `docs/evaluation.md` (measurement and runbook), `docs/operations.md` (environment, scale, cost), `docs/voice.md` (tone), `AI_USAGE.md`. The assignment is `docs/spec/case-study.pdf`.

## Stack
Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, PostgreSQL 16 + pgvector (halfvec, `chunks` hash-partitioned by owner), Celery + Redis, pdfplumber / python-docx, OpenAI SDK (Responses API for generation, analysis, extraction, judging; embeddings; an Anthropic adapter is dormant), a reranker service (fastembed cross-encoder), OpenTelemetry + grafana/otel-lgtm, React 18 + Vite UI, pytest, ruff, mypy, Playwright. docker-compose runs everything. Use only uv-managed Python 3.12 environments.

## Layout
`backend/app/{api,core,db,documents,storage,ingestion,retrieval,chat,insights,feedback,llm,explain,metrics,observability,worker}` · `backend/prompts/<id>@<version>.md` · `backend/config/` (retrieval and abstention YAML) · `backend/tests/{unit,integration,e2e,live}` · `frontend/` · `reranker/` · `eval/` (datasets, graders, judges, baselines) · `ops/otel/` · `scripts/` · `docs/`

## Commands
`make up` / `down` · `make ci` (lint, types, unit, frontend checks; no Docker) · `make test` (needs `make up`) · `make test-integration` (after `make up-test`) · `make ui-e2e` (fake model) · `make migrate` · `make diagrams` · `make eval-retrieval-fast`, `make eval-answers-v2 SPLIT=dev MAX_USD=1`, `make eval-gate`, `make eval-gate-t0`. Targeted tests while developing: `cd backend && uv run pytest path::test`.

## Hard rules
- **Root cause first.** At every failure, regression, timeout or inefficiency, find the cause from evidence (logs, rows, step timings, stored runs, paired comparisons) and rule out the alternatives before changing anything. No fix or explanation rests on an assumption; state what the data shows.
- Every model call goes through `app.llm.gateway`. Never instantiate provider clients elsewhere.
- Citations are application-managed: evidence sentences get handles (`E3.1`), the model returns handles in a strict schema, and code validates them and copies `cited_text` from the stored sentences. Never trust model-written quotes; an unknown handle is dropped (`CITATION_UNRESOLVED`).
- Model choice is code: only `app/llm/routing.py` and settings name models; nodes request a role (`fast`, `balanced`, `deep`, `flagship`). The LLM never picks its own model. Check effort support per model (`gpt-6.1-sol` rejects `none`).
- The LLM never widens scope, writes SQL, or picks control flow; it returns typed decisions that code applies.
- Only `chunks.original_text` is evidence. Prefixes and summaries are never cited. Evidence text comes only from the glyph-mapped character stream (invariant I1); never re-normalize `original_text`.
- Every repository method takes `owner_id` explicitly; every new resource gets a negative cross-user test (expect 404).
- Explanations come from reason codes and templates, never from asking the model to explain itself.
- Delivery is at-least-once: every consumer (task, stage, POST handler) must be idempotent. Job state lives in `processing_jobs` (leases, CAS claims), not in Celery.
- Never hold a DB connection across an LLM call or stream. Never run CPU-bound work on the API event loop.
- No document text, prompts, or completions in logs or spans: IDs and digests only. Metric labels are bounded enums; IDs go to traces and logs.
- Answers are never cached. The model never writes markdown tables in answers.
- Prompts are versioned files; a change is a new version and needs an evaluation run. Behaviour changes ship disabled behind a switch and are enabled only through a paired dev-split evaluation (`docs/evaluation.md` section 1.5); never tune on test-split content.
- Cost discipline: live tests, evaluations and probes run on samples with a `--max-usd` cap; never run a full dataset to validate a change. Keys live only in the ignored `.env`.
- No new dependency, table, endpoint or service without stating why in the change.

## Conventions
- Smallest correct diff; no unrelated refactors, no speculative abstractions, no comments that restate the code.
- Write the test first; name tests after the behaviour. Check an acceptance claim with a command you ran, not by reading code.
- Before claiming a file, function, flag or endpoint exists, grep for it. Report what was verified and what was not.
- `make ci` must pass before a change is done. Ruff formats and lints, mypy is strict on `backend/app`.
- Doc changes: keep `README.md`, `ARCHITECTURE.md`, `docs/*.md` and `CLAUDE.md` present-tense and free of plans, task ids and history. Tests in `backend/tests/unit/docs/` check links, paths, commands and the generated turn diagram.
- Regenerate generated artifacts instead of editing them: `make diagrams` (turn diagram), `make ui-types` (frontend API types), `scripts/export_postman.py` (Postman collection).

## Model IDs
Roles to defaults (no date suffixes; change via `MODEL_*` settings): `fast` = `gpt-6-luna`, `balanced` = `gpt-6.1-terra`, `deep` = `gpt-6.1-sol`, `flagship` = `gpt-6-astra` (gold audit and judge calibration only); embeddings `text-embedding-3-small`. Check SDK usage against current docs before writing; use the Responses API.

## Machine hygiene
- One eval- or ingestion-heavy command at a time; no loops of full evaluations. Prefer replay (`eval.replay`), retrieval-only (`make eval-retrieval-fast`) and the train or smoke split.
- Run the full gates (`make ci`, `make test`) once per change set, not per edit.
- Containers have CPU and memory caps in `docker-compose.yml`; Postgres runs with fsync off (dev and test only). Stop `otel-lgtm` and `reranker` when idle (`docker compose stop otel-lgtm reranker`); retrieval degrades to fusion order without the reranker.
- Check `uptime` before heavy work and wait if the load average is above about 8.
