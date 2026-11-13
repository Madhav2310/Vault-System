# Handoff: start of plan v2 coding (2026-11-13)

Repo `~/dev/vault` (the old Desktop path is a symlink). Read in this order:
1. `CLAUDE.md`
2. `docs/plan/v2.md` (why)
3. `docs/plan/README.md`, section "Plan v2"
4. the slice file you work on (`docs/plan/S11.md`…`S15.md`)
5. the tail of `docs/progress.md` and `docs/followups.md` (F-040…F-047)

## State
- `main` is clean. Gate: `make ci` 257 passed, `make test` 514 passed (20 opt-in skipped). No live agents, no open worktrees.
- Working end to end: upload → ready → ask → cited answer → history → metrics (`scripts/demo_core.sh`, live, $0.002 per ask).
- Eval tools: `make eval-smoke`, `make eval-retrieval` (full pipeline, ~$0.70, slow; S11.01 adds the fast mode), `python -m eval.judge`, `eval/engine`.
- Measured baselines:
  - recall@8 0.759 (the reranker hurts; lexical search is nearly useless)
  - citation integrity 1.00
  - smoke abstain_precision 0.50
  - p50 latency 5.9 s
- Spend to date is about $2.

## How to execute (Sonnet session)
- **Order:** S11 ∥ S12 → S13 ∥ S14 → S15. The main session takes the critical path (S11, then S13). Worktree lanes (`isolation: "worktree"`, model sonnet) take S12, then S14, then S15.01–S15.05.
- **Lane briefs:** use the full-context template from CLAUDE.md "Parallel work": owned files, files not to touch, the task text verbatim, ACs as the test contract, gate commands, and report format.
- **Merge gate per lane:** read the diff, then run `make ci && make test` on the merged tree, plus `make c1-check` when ingestion is touched and `make eval-retrieval-fast` when retrieval is touched. Then tick with `scripts/plan_tick.py` and run `python3 scripts/plan_lint.py`.
- **A [T] target that is not met:** record the measured value in `docs/progress.md` and stop for the user. Never relax an [A] criterion.
- **Live calls:** always `--max-usd`. Keys are only in `.env`.
- **Checkpoint rule:** at each slice gate, run an independent review agent over the slice diff, fix or file its findings, then report to the user.

## First three moves
1. **S11.01 (main):** `eval/pipeline_retrieval.py` plus `make eval-retrieval-fast`. Everything in S11 is measured with it.
2. **Launch the S12 lane in parallel:** S12.01 → S12.04 first. Owned files: `app/insights/**`, `backend/prompts/brief_*`, `backend/prompts/summary_custom@1.md`, `tests/**/insights/**`. Shared exception: S12.04 adds `brief` to STAGE_ORDER, and the lane must report that diff for review.
3. **S11.02 replay, then S11.03 lexical OR, then the S11.05 bake-off.** Use the stored candidates of eval run `01a1d43d-2bfe-7691-a250-7e3cd71c1d8f` as the "before".

## Open decisions for the user
- Reranker licence: resolved by the measurement in S11.05.
- Delete the parked copies `~/Desktop/Vault Document system.old.nosync` and `.claude/stale-worktrees.nosync` when iCloud releases them.
