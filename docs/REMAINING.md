# What is left (2026-11-13)

## How done are we?
| Measure | Value | Basis |
|---|---|---|
| v3 plan ACs ticked (S16–S20) | **41 / 231 = 18%** | `- [x]` counts in `docs/plan/S16–S20.md`. Understates progress: S17/S18 carry-overs and S19/S20 are untouched. |
| Whole assignment, effort-weighted | **about 45%** | Weights below, my estimate, not a measurement. |

| Deliverable | Weight | Done | Notes |
|---|---|---|---|
| Backend: upload, ingestion, hybrid retrieval, cited chat, briefs, metrics APIs | 30% | 90% | Works end to end; 669 tests pass; 19/19 corpus check. |
| Quality and speed (retrieval re-baseline, answer eval, streaming, fast path) | 20% | 25% | recall@8 0.74, 5.9 s p50 answers. Eval harness ready; not yet measured with the working reranker. |
| New product features (compare, conflicts, meta, why panel, stale answers, quota) | 10% | 5% | S18, not started. |
| Web UI (library, source guide, streaming chat, citation viewer) | 20% | 0% | S19; the biggest visible gap. |
| README, AI_USAGE, demo, eval write-up, scale doc | 20% | 15% | Skeleton + quickstart exist. |

## Left, in order (docs/plan/v3.md section 2)
1. **S16 gate** (hours): S16.08 AC5 ingestion time table, STATUS refresh, `make ci && make test`. Reranker bench met (p50 351 ms, 230 MB). Quickstart ran in 40 s.
2. **S17** (about 14 tasks): turn graph and concurrency (S17.14); re-baseline retrieval with the reranker (S17.02); listwise rerank, identifier tokens, contextual headers (S17.03–05); `retrieval@2` (S17.06); whole-document route (S17.07); evidence gate (S17.08); support check (S17.09); fast path (S17.10); hygiene (S17.11); answer baseline gate (S17.13, targets: answer_pass ≥ 0.80, faithfulness ≥ 0.90).
3. **S18** (12 tasks): SSE streaming + TTFT, why-panel endpoint, follow-ups, meta route, compare, conflicts, quota, stale answers, feedback metrics, OpenAPI/Postman.
4. **S19** (7 tasks): Vite/React UI served at `/app`, fake-LLM e2e harness, library, document page, chat with hover/click citations, accessibility, UX review.
5. **S20** (8 tasks): final README, diagrams, evaluation and scale docs, AI_USAGE.md (≥10 "AI was wrong" rows), demo script and recording, fresh-clone gate.

## Needs you
- Approve each slice gate (S16.07, S17.13, S18.12, S19.07, S20.08).
- Record the demo video; try the UI at S19.
- Optional: label 40 answers for judge calibration (S20.07).
- Delete parked `Vault Document system.old.nosync` and `.claude/stale-worktrees.nosync` once iCloud releases them.

## Risks
- Quality targets may miss; I record the number and stop rather than relax them.
- The UI is the largest unbuilt piece. If time is short, cut order: S17.03, S17.05, S18.06/07, then S19.06.
- Spend so far about $3 of the $30 cap.
- Open followups F-041..F-048 in `docs/followups.md`.
