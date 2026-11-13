# Vault — evidence-first document intelligence (take-home)

Spec: `docs/spec/case-study.pdf`. **Design (source of truth): `docs/design.md`.** Plan (checklist + atomic acceptance criteria): `docs/plan/README.md`. **Plan of record since 2026-11-13: v2 = `docs/plan/S11.md`…`S15.md`** (rationale `docs/plan/v2.md`); unfinished S0–S10 tasks are deferred unless a v2 task names them. Rationale/sources: `docs/research/01–06` (05 = PDF geometry chain, 06 = convergence engine). Eval set: `eval/datasets/vault_eval_v1/` (rebuild via `eval/build/*`; third-party sources in gitignored `eval/sources/`). Progress log: `docs/progress.md` and AI usage log: `AI_USAGE.md` (both created in S0).

## Stack
Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, PostgreSQL 16 + pgvector ≥0.8 (halfvec, hash-partitioned `chunks`), Celery + Redis, pdfplumber / python-docx, OpenAI SDK (Responses API for generation, analysis, extraction and judging; embeddings; Anthropic adapter kept dormant), reranker service (fastembed cross-encoder), OpenTelemetry + grafana/otel-lgtm, pytest, ruff, mypy, Locust. docker-compose for everything.

## Layout
`backend/app/{api,core,db,documents,storage,ingestion,retrieval,chat,insights,matrix,feedback,llm,explain,observability}` · `backend/prompts/<id>@<version>.md` · `backend/tests/{unit,integration}` · `reranker/` · `eval/` · `loadtest/` · `ops/{otel,grafana}`

## Commands
`make up` · `make test` · `make lint typecheck` · `make migrate` · `make seed` · `make eval` / `eval-retrieval` / `eval-smoke` · `make loadtest` (exact targets defined in S0)

## Hard rules
- Every model call goes through `app.llm.gateway`. Never instantiate provider clients elsewhere.
- Citations are application-managed: evidence sentences get handles (`E3.1`), the model returns handles in a strict schema, and code validates them and copies `cited_text` from the stored sentences. Never trust model-written quotes; an unknown handle is dropped (`CITATION_UNRESOLVED`).
- Model choice is code: only `app/llm/routing.py` (and settings) names models; nodes request a role (`fast`/`balanced`/`deep`/`flagship`). The LLM never picks its own model. Check effort support per model (`gpt-6.1-sol` rejects `none`).
- Cost discipline: live tests, spikes and evals run on samples with a `--max-usd` cap; never run a full dataset to validate a change. Keys live only in the gitignored `.env`.
- Only `chunks.original_text` is evidence. Prefixes and summaries are never cited.
- Every repository method takes `owner_id` explicitly; every new resource gets a negative cross-user test (expect 404).
- The LLM never widens scope, writes SQL, or picks control flow; it returns typed decisions that code applies.
- Explanations come from reason codes + templates, never from asking the model to explain itself.
- Delivery is at-least-once: every consumer (task, stage, POST handler) must be idempotent (design §14).
- Job state lives in `processing_jobs` (leases, CAS claims), not in Celery.
- Never hold a DB connection across an LLM call or stream. Never run CPU-bound work on the API event loop.
- No document text, prompts, or completions in logs or spans — IDs and digests only.
- Metric labels are bounded enums only; IDs go to traces/logs/exemplars.
- Answers are never cached. The model never writes markdown tables in answers.
- Evidence text comes only from the glyph-mapped character stream (research 05, invariant I1); never re-normalize `original_text`.
- Tuning changes are accepted only through the convergence engine's paired test decision (research 06); never tune on test-split content.
- Prompts are versioned files; changing one bumps its version and requires an eval run.
- No new dependency, table, endpoint, or service outside `docs/design.md` without stating why.

## Per-slice workflow
1. At slice start: re-read `docs/plan/S<n>.md` and the referenced design sections; propose any [T] or task-shape changes to the user before coding.
2. Work one task at a time, in ID order unless dependencies allow otherwise. Write the task's named tests first (test names in ACs are part of the contract).
3. Implement the smallest complete change; one commit per task, message prefixed with the task ID.
4. Check each AC box only with evidence; run `make lint typecheck test`; record the progress entry and AI_USAGE.md update (see `docs/plan/README.md` → Definition of done).
5. A [T] that isn't met: record the measured value and stop for the user. Never relax [A].
6. Any edit to plan files → `python3 scripts/plan_lint.py` must exit 0.
7. Report VERIFIED vs UNVERIFIED. Stop at the slice gate; don't start the next slice unasked.
8. Core path (plan README → M0): work checkpoints C1 → C2 → C3; stop and report at each checkpoint, after a review pass of the diff since the previous one.

## Parallel work (user instruction 2026-11-13: parallelize throughout, highest quality)
- **Lanes**: the main session (orchestrator) runs the critical-path task; independent tasks go to worktree agents (`isolation: "worktree"`, model `sonnet` by default; Opus only for design-heavy or ambiguous tasks). A task is parallel-safe when its Deps are done and its files don't overlap another live lane's.
- **Brief = full context**: task IDs with the plan text, design/research sections to read, owned files (exclusive), files not to touch, conventions, commands, and the report format. Agents read CLAUDE.md and their design sections before coding and write the named tests first.
- **Single writer for shared files**: only the main session edits `docs/progress.md`, `docs/followups.md`, plan ticks, `AI_USAGE.md`, `uv.lock`/`pyproject.toml` (agents request dependencies instead of adding them), `docker-compose.yml`, `Makefile`, migrations. Agents put evidence, deviations and follow-ups in their final report.
- **Shared infra**: agents use the running compose Postgres with their own test DB (per-checkout name, F-014); they never run `make up/down/migrate`, never touch the dev DB, and run `make ci` and `make test-integration` (e2e runs in the main checkout after merge).
- **Merge gate (main session)**: read the diff, run `make ci && make test` on the merged tree plus any corpus check the task touches (`make eval-extraction` for extraction), then tick ACs and record progress. A lane that fails review goes back with specific findings; nothing is ticked on an agent's word.
- **Close agents** when their lane is merged; no idle agents.

## Model IDs
Roles → defaults (no date suffixes, change via `MODEL_*` settings): `fast` = `gpt-6-luna`, `balanced` = `gpt-6.1-terra`, `deep` = `gpt-6.1-sol`, `flagship` = `gpt-6-astra` (judge calibration and stage E only); embeddings `text-embedding-3-small`. Routing table: design §4.2. Check SDK usage against current docs before writing — don't guess API shapes (use the Responses API).
