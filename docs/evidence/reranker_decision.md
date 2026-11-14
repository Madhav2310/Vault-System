# Reranker decision (S17.02, 2026-11-14)

**Decision:** the default retrieval config (`retrieval@2`) uses fusion order with no reranker (`rerank.kind: none`). The MiniLM cross-encoder stays available as `retrieval@2-rerank` (needs `make up-rerank`).

**Rule (written before the measurement, plan S17.02):** keep the cross-encoder only if the paired verdict is "B better", or there is no detectable difference with MRR@40 better by 0.03 or more, *and* its p50 added latency is 400 ms or less.

| Measure (train, 40 paired items) | no rerank | MiniLM windowed | Verdict |
|---|---|---|---|
| recall@8 | 0.733 | 0.683 | no detectable difference (delta -0.050, CI [-0.164, +0.058], p=0.377) |
| mrr@40 | 0.480 | 0.554 | no detectable difference (delta +0.074, CI [-0.032, +0.182], p=0.170) |
| added latency | - | p50 rerank step 495 ms (53 turns; p50 of `retrieve` step 608 ms) | above the 400 ms bar |

The container alone measures 351 ms at 20 passages (S16.02); the 495 ms in served turns includes loading passages and building focus windows.

**Consequence:** the rule fails on latency, so `kind: none`. The effect on final answers was measured once with the cross-encoder (first answer run, `answer_pass` 0.614) and then with the new default (see `docs/evidence/answer_ab.md`).

**Reproduce:** `make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2-norerank`, then `... RETR_CONFIG=retrieval@2-rerank`, then `python -m eval.paired <b>.json <c>.json --metric recall@8`.
