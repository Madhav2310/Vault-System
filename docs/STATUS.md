# Vault: status report (2026-11-14, after plan v4 build)

One page. Every number is measured; each has its source or command.

## 1. Where we are
All spec features are built. Plan v4 (evaluation rebuild S21, accuracy and speed changes S22, warm voice and chat S23) has its code merged; every S22/S23 behaviour change is **off by default** until a paired A/B on the dev split accepts it (rule: `docs/plan/S22.md`). Gates on main: `make ci` 728 passed; `make test` 1289 passed, 1 flaky (F-048 trace test, passes alone); `make ui-e2e` 58 passed (last full run on the S23.02 lane).

## 2. The new measuring instrument (S21)
- Golden set v2: 316 live items over 189 documents, 15 strata, at most 3 per document, split by document (train 123 / dev 59 / test 134 items). Gold is atomic claims. Gold is unaudited until the human review (`eval/datasets/vault_eval_v2/audit_review.md`).
- Claim-level judges, clustered CIs, minimum detectable effect, stage attribution, injection graders, gate tiers (`make eval-gate`), runbook `docs/eval_v2_runbook.md`.

## 3. First v2 train run (123 items, current main with all switches off, judges claims@1)
answer_pass 0.642, claim_completeness 0.691, support_rate 0.664, citation_integrity 1.000, injection violations 0, abstention_correct 0.600, recall@8 0.807, cite_chunk_precision 0.619, latency mean 5.2 s, $0.0057/turn. Cost $1.23, 24 min (`eval/reports/01a1d89d-...`).
Root cause of the 51 flagged items (`docs/evidence/train_v2_rca.md`): 27 real system failures (12 ranking cut, 5 retrieval miss, 5 generation omission, 3 wrong abstain, 2 generation error) and 24 grader or gold artifacts (15 judge too strict, 9 gold wrong or incomplete).
Re-judge of the same answers with the fixed judges (claims@2, support@2, versioned fix; $0.51): answer_pass 0.659, support_rate 0.842, abstention_correct 0.840. Not a baseline: provisional until the 200 human labels.

## 4. What needs the user
1. Review the gold audit queue: `eval/datasets/vault_eval_v2/audit_review.md` (25 items to read, 44 to skim). Proposals are heuristics; a gold fix that only deletes claims the system fails would bias the set toward the system.
2. Label 200 (item, answer) pairs for judge calibration (`make eval-labels`), S21.06 AC3/4.
3. Approve the A/B round (about $4-5): baseline + plan-first, support v2, calc, compare quota, voice, analyze v3; free retrieval-only arms for lexical v2 and chunker@2.
4. Review the chat UI at `/app` and `docs/voice.md`.

## 5. Spend
Sum of the printed per-run spends this session: about $4.0 (train run $1.23, audit $1.88, re-judge $0.51, requeue $0.25, migration $0.04, table drafts $0.01) on top of $2.98 earlier, so about $7 of the $30 cap. The main checkout's ledger command (`python -m eval.replay --spend 2026-11-01T00:00:00+00:00`) prints $3.76: it does not see spend recorded by lane worktrees, so treat the sum as the better figure.

## 6. Open risks
Gold unaudited; judges not human-calibrated; extraction/chunking stages not observable in attribution (shows 0: unmeasured); `table_lookup` is model-drafted gold (unaudited); `formatting` stratum has 0 of 5 items; ingestion of the eval corpus took about 25 min on this laptop.
