# Answer-quality baseline and A/B (S17.12 / S17.13, 2026-11-14)

Train split, 50 items, full pipeline through the HTTP API with binary judges. Every run: degraded_rate 0.000, citation_integrity 1.000. Spend per run well under the $3 cap (about $0.006 per turn answering, plus judges and ingestion).
Command: `make eval-answers SPLIT=train MAX_USD=3` (add `EVAL_ARGS="--freeze answer"` to freeze `eval/baselines/answer.json`).

| Run | Config | answer_pass | faithfulness_pass | cite_chunk_precision | abstain_precision | latency_ms (mean) | cost/turn |
|---|---|---|---|---|---|---|---|
| 1 | retrieval@2 with MiniLM rerank, gate floor -5.44 | 0.614 [0.455, 0.750] | 0.886 [0.773, 0.955] | 0.633 [0.496, 0.775] | 0.857 | 5969 | $0.0063 |
| 2 (frozen baseline) | retrieval@2 = no rerank, gate floor 0.38 | 0.636 [0.500, 0.773] | 0.841 [0.727, 0.932] | 0.619 [0.485, 0.744] | 0.600 | 5476 | $0.0063 |

Targets (plan S17.13): answer_pass >= 0.80, faithfulness >= 0.90, cite_chunk_precision >= 0.85. **All three are missed** (recorded, not relaxed). Differences between runs 1 and 2 are inside the confidence intervals (n = 44 items).

Support-check calibration (`python -m eval.support_calibration --n 40 --max-usd 0.5`): `support_check@1` vs the faithfulness judge: n = 37, agreement 0.92, kappa 0.83, spend $0.138.
