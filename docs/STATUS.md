# Vault: status report (2026-11-14)

One page. Every number is measured; each has its source or command.

## 1. Where we are
Every feature in the spec, including the bonus list, is built and merged (sentiment is the brief's tone). Quality is measured; three answer-quality targets are missed (section 3).

| Pillar | State | Evidence |
|---|---|---|
| Upload and async ingestion | Done. AMD 10-K (121 pages) searchable in 20.2 s (28.8 s before parallel embedding) | `docs/scale.md` |
| Cited chat, streaming, multi-turn | Done. Citation integrity 1.00. Demo TTFT p50 3.3 s, latency p50 6.1 s | `scripts/demo_core.sh` |
| Answer routes | RAG, whole-document, meta (SQL), compare, conflicts, stale answers with re-ask, support check per citation, evidence gate | `docs/evidence/answer_ab.md` |
| AI insights | Brief with summary, key facts, tone, questions, all cited; custom summaries | `eval/brief_eval.py` |
| Metrics APIs and UI | Documents, processing, chat (node latency, TTFT, cost, feedback, quota); web UI at `/app` | `make ui-e2e` (41 specs, axe 0 serious) |
| Docs and demo | README final, scale and architecture docs; demo recording and README numbers pending | `README.md` |

Gates on main: `make ci` 483 passed; `make test` 905 passed, 1 flaky (F-048, passes alone); `make ui-e2e` 41 passed.

## 2. Decisions made from measurements (pre-set rules, paired statistics)
- **No cross-encoder by default:** no detectable quality difference, adds p50 495 ms. Kept as `retrieval@2-rerank`. `docs/evidence/reranker_decision.md`.
- **Whole-document route stays opt-in:** no detectable quality difference, twice the cost. `docs/evidence/answer_ab.md`.
- **Evidence gate off:** calibrated to 4.9% false abstention on retrieval, but on answers it refused 3 of 44 answerable items.
- **Support check kept:** agrees with the faithfulness judge 92% (kappa 0.83).

## 3. Answer quality (train, 50 items, frozen baseline `eval/baselines/answer.json`)
| Metric | Value [95% CI] | Target |
|---|---|---|
| citation_integrity | 1.000 | 1.00 (met) |
| answer_pass | 0.636 [0.500, 0.773] | 0.80 (missed) |
| faithfulness_pass | 0.841 [0.727, 0.932] | 0.90 (missed) |
| cite_chunk_precision | 0.619 [0.485, 0.744] | 0.85 (missed) |
| cost per turn | $0.0063 | |

Root cause of the 16 failures: 8 retrieval misses (QASPER papers, 10-K tables), 3 false gate abstentions (now off), about 5 judged "incomplete" where at least 2 answers are correct (the completeness judge is too strict), 3 compare/conflict items missing one side.

## 4. Next (proposed)
1. Calibrate the completeness judge against human labels (S20.07, 40 items) before tuning against it.
2. Accuracy lane: answer prompt (complete conditions and dates, minimal citations), per-document evidence for compare, table-aware chunking for the misses; one train A/B (about $0.40), confirm once on test.
3. Live latency check of speculative retrieval and the wider fast path (built and tested offline).
4. Deferred: README numbers, demo recording, AI_USAGE prompt-log rows, final fresh-clone gate.

Spend since 2026-11-01 from the ledger: $2.98 of the $30 cap (`python -m eval.replay --spend 2026-11-01T00:00:00+00:00`).
