# Retrieval A/B log (plan S11)

Rows are appended per experiment. Regenerate with the command in the last column (backend stack up: `make up`).

| date | split | config | recall@8 | mrr@40 | wall_s | cost | command |
|---|---|---|---:|---:|---:|---:|---|
| 2026-11-13 | all (100 items with gold) | retrieval@1 (MiniLM rerank, AND lexical) | 0.744 | 0.508 | 145 (warm) | ~$0.0001 | `make eval-retrieval-fast` |

## Replay of stored candidates, eval run 2026-11-13 (`python -m eval.replay --run latest --rank X`)
Per-quote recall@8 (133 quotes / 75 items with gold; replay command from `backend/`: `PYTHONPATH=.. uv run --frozen python -m eval.replay --run latest --rank rrf --by-category`).

| ranking | quote_recall@8 | item_recall@8 | recall@20 | recall@40 | note |
|---|---:|---:|---:|---:|---|
| rrf (fusion) | 0.639 | 0.701 | 0.789 | 0.827 | |
| vector only | 0.632 | 0.704 | 0.737 | 0.789 | |
| lexical only (AND query) | 0.150 | 0.153 | 0.165 | 0.165 | |
| rerank (only runs that were reranked) | 0.173 | 0.273 | - | - | **126 of 159 runs had no rerank** |
| final (as served) | 0.654 | 0.727 | 0.789 | 0.827 | |

**Finding (supersedes the "reranker hurts" reading in docs/plan/v2.md):** the MiniLM reranker needs 1.3-2.6 s for 40 passages on this machine (3 probes) but the client timeout is 800 ms, so it degraded (`no_rerank`) in 79% of the eval runs. The served ranking was therefore mostly plain RRF; the earlier number compared against a sort that ranked missing scores last. The real reranker effect is unmeasured; S11.05 must measure with a timeout that lets it finish, and weigh its latency.

## S17.02 re-baseline with the fixed reranker (2026-11-14, train split, 50 items, 40 with gold)
Reranker container: 3 CPUs / 3 threads, p50 351 ms in isolation (S16.02). All three runs degraded_rate 0.000.

| Run | Config | recall@8 | mrr@40 | Command |
|---|---|---|---|---|
| (a) lexical only (OR query) | retrieval@2-norerank, EVAL_RANK=lexical | 0.633 | 0.350 | `EVAL_RANK=lexical make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2-norerank` |
| (b) fusion, no rerank | retrieval@2-norerank | 0.733 | 0.480 | `make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2-norerank` |
| (c) MiniLM, windowed | retrieval@2 | 0.683 | 0.554 | `make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2` |

Lexical-only recall rose from 0.150 (AND query) to 0.633 (OR query): S17.02 AC1 target (>= 0.30) met.

Paired (b) vs (c), `python -m eval.paired <b>.json <c>.json --metric <m>`, 40 paired items:
- recall@8: delta -0.050, 95% CI [-0.164, +0.058], p = 0.377. verdict: **no detectable difference**.
- mrr@40: delta +0.074, 95% CI [-0.032, +0.182], p = 0.170. verdict: **no detectable difference**.

Gate calibration (`python -m eval.calibrate_gate --split train --config retrieval@2 --write`): floor = -5.44, false abstain 0.0488 (target <= 0.05), true abstain 0.50 on the unanswerable items.
