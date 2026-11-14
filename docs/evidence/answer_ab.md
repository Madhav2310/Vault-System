# Answer-quality baseline and A/B (S17.12 / S17.13, 2026-11-14)

Train split, 50 items, full pipeline through the HTTP API with binary judges. Every run: degraded_rate 0.000, citation_integrity 1.000. Spend per run well under the $3 cap (about $0.006 per turn answering, plus judges and ingestion).
Command: `make eval-answers SPLIT=train MAX_USD=3` (add `EVAL_ARGS="--freeze answer"` to freeze `eval/baselines/answer.json`).

| Run | Config | answer_pass | faithfulness_pass | cite_chunk_precision | abstain_precision | latency_ms (mean) | cost/turn |
|---|---|---|---|---|---|---|---|
| 1 | retrieval@2 with MiniLM rerank, gate floor -5.44 | 0.614 [0.455, 0.750] | 0.886 [0.773, 0.955] | 0.633 [0.496, 0.775] | 0.857 | 5969 | $0.0063 |
| 2 (frozen baseline) | retrieval@2 = no rerank, gate floor 0.38 | 0.636 [0.500, 0.773] | 0.841 [0.727, 0.932] | 0.619 [0.485, 0.744] | 0.600 | 5476 | $0.0063 |

Targets (plan S17.13): answer_pass >= 0.80, faithfulness >= 0.90, cite_chunk_precision >= 0.85. **All three are missed** (recorded, not relaxed). Differences between runs 1 and 2 are inside the confidence intervals (n = 44 items).

Support-check calibration (`python -m eval.support_calibration --n 40 --max-usd 0.5`): `support_check@1` vs the faithfulness judge: n = 37, agreement 0.92, kappa 0.83, spend $0.138.

## Whole-document route A/B (S17.07 AC6), single-document items, train (2026-11-14)
Baseline = run 2 above (`FULL_CONTEXT_AUTO=false`); B = same items with `FULL_CONTEXT_AUTO=true` (37 of 152 turns used `mode=full_context`). Paired, 39 items:

| Metric | rag | full_context | Paired verdict |
|---|---|---|---|
| answer_pass | 0.692 | 0.641 | no detectable difference (delta -0.051, CI [-0.180, +0.077]) |
| cite_chunk_precision | 0.636 | 0.662 | no detectable difference (delta +0.025) |
| faithfulness_pass | 0.897 | 0.872 | no detectable difference |
| cost per turn | $0.0063 | $0.0120 | about 2x |
| latency (mean) | 5476 ms | 5973 ms | similar |

**Decision:** `full_context_auto` stays **false** (no quality gain, double the cost). The route remains available when requested. Command: `make eval-answers SPLIT=train MAX_USD=1.5 FILTER=scope_docs=1` with `FULL_CONTEXT_AUTO=true`.
