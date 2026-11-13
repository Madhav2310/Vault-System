# Handoff (2026-11-13, after C2/C3 spine)

Repo `~/dev/vault`. Read CLAUDE.md, docs/plan/README.md (Core path), tail of docs/progress.md and docs/followups.md.

## State
- main is clean and committed. Gate: `make ci` 257 passed, `make test` 514 passed. All lanes merged and closed; no live agents.
- Working end to end: upload -> ready -> ask -> cited answer -> history -> metrics (`scripts/demo_core.sh` OK live, $0.0019 per ask).
- Eval: `make eval-smoke` / `make eval-retrieval` (real HTTP pipeline `eval/pipeline_http.py`); judges validated (agreement 1.00, $1.11); engine core merged.
- Measured (pre-review): recall@8 0.759 (target 0.85 NOT met, S2.17 AC3 open), citation integrity 1.00. Spend so far about $2.
## Next
1. User decisions: accept recall gap and tune via convergence engine (S2.19/S4)? MiniLM vs bge reranker license.
2. S3.15 grounding baseline (judges over answers, capped), S2.14 wiring into the real pipeline (F-045), follow-ups F-041..F-047.
3. Core-path leftovers: S3.02 AC4, S3.05 AC2, S2.05 AC2, usage-on-refusal gateway issue.
