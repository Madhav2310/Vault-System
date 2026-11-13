# Vault: evidence-first document intelligence

Upload PDF, DOCX, TXT or Markdown files. Vault processes them in the background, writes a cited summary of each, and lets you chat with your documents. Every claim in an answer points to the exact sentence it came from.

> Status: under construction (plan in `docs/plan/v3.md`). Sections marked *(planned)* are not built yet.

## Quickstart

```bash
scripts/quickstart.sh     # needs Docker running and OPENAI_API_KEY in .env
scripts/demo_core.sh      # upload -> ready -> ask -> cited answer -> history -> metrics
```

Docker is only needed to run the stack; unit tests and lint need none (`make ci`). See `docs/dev-environment.md`.

## What works today

- [x] Upload with async processing, dedupe, versions, delete
- [x] PDF, DOCX, TXT, MD extraction with exact character positions
- [x] Hybrid retrieval (vectors + full-text) and cited chat with multi-turn memory
- [x] Citations copied by code from stored sentences (no invented quotes)
- [x] Cited document briefs and custom summaries
- [x] Metrics APIs (documents, processing, chat), rate limits, new document versions
- [ ] Streaming answers, web UI, whole-document answering, support check *(planned)*

## Architecture

*(diagram planned)* An API (FastAPI) accepts uploads and chat; Celery workers extract, chunk, embed and summarize; PostgreSQL with pgvector stores everything; Redis carries queues and caches; OpenAI models are called only through one gateway.

## Key design decisions

- **Citations are application-managed:** evidence sentences get handles like `E3.1`, the model returns handles in a strict JSON schema, and code copies the stored sentence. A model cannot invent a quote.
- **Hybrid retrieval:** vector search plus Postgres full-text, fused by reciprocal rank.
- **Whole-document answering for small scopes** *(planned)*.
- **Evidence gate:** answer only when the documents support it *(planned)*.
- **Model roles, not model names:** code requests `fast`, `balanced`, `deep`; routing lives in one file.

## Evaluation

Golden set of 122 items over 15 documents (`eval/datasets/vault_eval_v1`). Numbers and the commands that regenerate them: `docs/STATUS.md`. *(final tables planned)*

## Scale

*(planned)* See `docs/plan/v3.md`.

## Limitations and what was deliberately not built

Matrix comparison view, load-test suite and dashboards, and vision-based highlight QA were cut to keep the submission small and fully working.

## Repo map

`backend/app` (API, ingestion, retrieval, chat, insights, llm) · `backend/prompts` (versioned prompts) · `eval` (golden set, graders, judges, harness) · `reranker` (optional service) · `docs` (design, plan, evidence) · `scripts` (quickstart, demo)
