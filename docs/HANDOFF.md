# Handoff (written 2026-11-13 for a fresh session)

Repo `~/dev/vault` (symlinked from the old Desktop path). Read `CLAUDE.md`, `docs/plan/README.md` ("Core path"), the tail of `docs/progress.md` and `docs/followups.md`.

## State
- Committed on `main`; last full gate: `make ci` 192 passed, `make test` 400 passed, `make c1-check` 19 passed, `make eval-extraction` 100.0. Progress: 301/792 ACs (38%), core path 183/236 (77.5%). C1 (ingestion) closed after an independent review (16 findings fixed).
- Spend to date < $0.10 of the $50 limit ($30 engine budget).
- Dev stack: `make up` (compose; Postgres 5442, Redis 6389, API 8100, reranker 8180). Test DB is per checkout.

## Worker lanes still running or just finished (Sonnet, worktrees under `.claude/worktrees/agent-*`)
Their branches are NOT merged yet. If their notifications are lost, find them with `git worktree list` / `git branch --list 'worktree-agent-*'`, review each diff, run `make ci && make test` on the merged tree, tick ACs with `scripts/plan_tick.py`, write progress, then `git worktree remove --force` and delete the branch.
- **E: DONE, awaiting merge** (branch `worktree-agent-a5b8be7a54770b394`, commit 99170e7; lane reports `make ci` 188 passed and `make test-integration` 211 passed, all of AC1-AC6 passing; live run $0.0176: 3 lookup items each got a valid citation overlapping the gold quote, and the full-context repeat call showed `cache_read_tokens` 6483). Review its diff and run the gate before ticking S3.08. Findings to act on: (1) the gateway raises on refusal/truncation BEFORE yielding `Final`, so the generate node reports zero usage and no call ids for those results; S3.10 totals must be summed from `llm_calls` by run id, or attach usage/cost/call_ids to `RefusalError`/`TruncatedError` in `app/llm/errors.py` + gateway `stream`; (2) check the gold page mismatch for `cuad_consulting:governing_law` (gold says p.6, the cited chunk starts on p.5). Original brief: S3.08 tests + live sample (generate node `backend/app/chat/nodes/generate.py`; needs `cache_mode` pass-through to ChatRequest; live cap $0.05).
- **F: S3.10 evidence ledger (`app/chat/ledger.py`, `nodes/persist.py`) + S3.11 history endpoint.**
- **G: S2.12 eval harness + S2.13 golden-set integration (`eval/*`)**; it will ask for Makefile targets `eval-retrieval`/`eval-smoke`.

## Next steps (in order)
1. **S2.10 `retrieve()` facade** (main session): `config/retrieval@1.yaml` + `app/retrieval/config.py` are done and committed. Still to write: `app/retrieval/retrieve.py` (`Retriever.retrieve(session, owner_id, scope, query)`: Redis result cache keyed sha256(owner, normalized standalone query, sorted version ids, config version), embed query (degrade to lexical-only), `vector_search` + `lexical_search`, `fuse`, load passage texts (embedding_text, owner-filtered), `RerankClient.rerank` top_n, `expand_neighbors`, candidate records with ALL `retrieval_candidates` fields: vector rank/distance, lexical rank/score, id_match, rrf score, rerank score, selected, expanded_from; degraded list; do not cache degraded results) and a loader `chunks -> EvidenceChunk` (join documents.title, document_versions.version_no, chunks.sentences JSON). Note `VectorHit` has `distance` (no `score`): adapt as `Ranked(chunk_id, version_id, 1 - distance)`. Tests: `integration/retrieval/test_retrieve.py::test_candidate_fields_complete`, `unit/retrieval/test_cache_key.py::{test_version_change_misses,test_config_version_misses}`, `unit/retrieval/test_config.py::test_schema_strict`.
2. **Turn orchestrator (core-path glue, not a plan task):** `app/chat/turn.py` wiring analyze_query → scope narrow → retrieve → (expand) → build_evidence → generate → resolve → persist_turn → release lease; make `POST /v1/chat/sessions/{id}/messages` run it (the endpoint currently only creates the user message and a `running` run). Plus `scripts/demo_core.sh` (upload → ready → ask → cited answer → history → metrics) for the C3 exit check.
3. **S2.18 judges** (needs lane G's schema), **S2.14 eval engine core**, **S2.17 retrieval metrics + `make eval-retrieval`** (recall@8 >= 0.85 target), **S3.15 grounding baseline** (live, sampled, `--max-usd`).
4. Merge gate + independent review pass for C2 and C3, then report to the user (rule: stop at each checkpoint).

## Open decisions for the user
MiniLM reranker license question (MS MARCO terms) vs bge-reranker-base (MIT, ~6x slower); reranker latency re-measure on an idle machine (F-033); delete `~/Desktop/Vault Document system.old.nosync` and `.claude/stale-worktrees.nosync` when iCloud lets go.
