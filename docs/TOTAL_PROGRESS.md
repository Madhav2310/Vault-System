# Vault: total progress and what is left (2026-11-14)

Method: counts come from the plan files (`docs/plan/S16–S23.md`) and from merged commits and measured runs. **Caveat:** the tick boxes in the plan files are stale (S17, S18 and S20 have built work still unticked; S21–S23 were never ticked), so the tables below use the merged-code and evidence view, and reconciling the ticks is itself a small task (item 14 below).

## 1. One-paragraph summary
Every feature in the case study is built and running (upload, async ingestion, insights, cited streaming chat, metrics APIs, web UI, scale work). What remains is almost entirely **proof and decisions**: the new golden set needs your review and labels, the 7 behaviour changes built this session are all switched off until paired A/B runs accept them, accuracy is not yet at the 95% goal (first honest v2 number: answer_pass about 0.64 to 0.66 on train), and the README numbers, demo recording and final fresh-clone gate are still pending.

## 2. Completion by area (rough, honest)
| Area | State | Share |
|---|---|---|
| Spec features (incl. bonus list) | built and merged | about 100% |
| Plan v3 (S16–S20) | 171 of 232 acceptance checks ticked (74%); real figure higher, ticks stale | about 85% |
| Plan v4 code (S21–S23, 28 tasks) | code merged for 24 tasks (86%) | 86% |
| Plan v4 proven (acceptance evidence in hand) | 7 of 28 tasks fully done | 25% |
| Answer quality vs targets (0.80 pass, 0.90 faith, 0.85 cite precision) | not met; first v2 train baseline 0.64 pass, 0.84 support (re-judged) | about 60% of the way |
| Docs and demo (README numbers, demo, AI_USAGE complete) | README final minus numbers; demo not recorded; AI_USAGE current | about 55% |

Gates on main today: `make ci` 728 passed; `make test` 1289 passed + 1 known flaky (F-048); `make ui-e2e` 58 passed.

## 3. Plan v4 task by task (28 tasks)
Legend: DONE = acceptance evidence in hand; BUILT = code merged, behaviour off or tool built, proof pending; TODO = not started.

| Task | What | State | What is missing |
|---|---|---|---|
| S21.01 | Gold schema v2 | DONE | |
| S21.02 | Migrate 122 v1 items | DONE | $0.037 spent |
| S21.03 | Author strata + fixtures | DONE | formatting stratum 0 of 5 (see item 11) |
| S21.04 | Public strata (+ table drafts) | DONE | table_lookup 24 of 25 |
| S21.05 | Cross-model gold audit | BUILT | **your review of the audit queue** (25 read, 44 skim) |
| S21.06 | Claim/support/abstention judges | BUILT | **your 200 labels**, then kappa and TNR check |
| S21.07 | Clustered CIs, MDE, Clopper-Pearson | DONE | |
| S21.08 | Stage attribution | DONE | extraction/chunking not observable yet (reads 0) |
| S21.09 | Security + injection suite | BUILT | injection 0 violations on 9 train items, needs test split; `/security-review` run |
| S21.10 | Baseline + gate tiers | BUILT | freeze `answer_v2` on test after audit |
| S21.11 | S21 gate | TODO | after the above |
| S22.01 | chunker@2 | BUILT | A/B (needs re-ingest of dev docs) |
| S22.02 | Contextual index text | BUILT | A/B (deterministic first, $0 ingest) |
| S22.03 | Lexical fixes | BUILT | v1 A/B done: no detectable difference; re-run on v2 table/number strata |
| S22.04 | Plan-first answers | BUILT | paired A/B |
| S22.05 | Compare quotas | BUILT | paired A/B on compare/conflict strata |
| S22.06 | Verify-and-repair | BUILT | paired A/B; decide turn budget 4 vs 5 |
| S22.07 | Scored abstention | BUILT | calibration run with flags on, fit, then A/B |
| S22.08 | Calculation segments | BUILT | paired A/B on numeric_calc |
| S22.09 | Latency | BUILT | live TTFT measurement (`make eval-smoke EVAL_ARGS=--stream`), analyze_query@3 A/B |
| S22.10 | Locate loop | BUILT | A/B on 10-K strata |
| S22.11 | Security hardening | DONE | cookie auth, CSRF header, CSP, ui-e2e 58 |
| S22.12 | Deferred arms | TODO | only if residual failures justify |
| S22.13 | S22 gate | TODO | one test-split run of the final config |
| S23.01 | Warm voice | BUILT | voice A/B; **your approval of `docs/voice.md` examples** |
| S23.02 | Clarify when ambiguous | BUILT | review; flag off by default |
| S23.03 | Chat interaction polish | BUILT | **your UI review**; polish lane running now |
| S23.04 | S23 gate | TODO | |

## 4. What is left, in order
**A. Needs you (human time, no spend)**
1. Review the gold audit queue `eval/datasets/vault_eval_v2/audit_review.md` (about 15 minutes). Decisions go in `audit_queue.yaml`; then `make eval-audit-apply`.
2. Label 200 pairs (`make eval-labels`) for judge calibration (about 1 to 2 hours). Until then the judge numbers are provisional.
3. Review the chat UI at `localhost:8100/app` and the examples in `docs/voice.md`.
4. S16.07 sign-off (read `docs/STATUS.md`).

**B. Needs live spend (ask first; caps in each command)**
5. A/B round on dev (59 items, about $0.6 per arm): baseline, then plan-first, support v2, calc, compare quota, voice, analyze v3 (about $4 to $5 total).
6. Free arms first: chunker@2 and `deterministic` contextual index (re-ingest of 36 dev documents, about 10 minutes each arm), lexical v2 on the v2 strata.
7. Calibrate scored abstention (run with plan-first and support v2 on), fit `abstain@1.yaml`, one A/B.
8. Live TTFT measurement and decision on analyze_query@3 (target p50 at most 2.0 s, now 3.3 s).
9. Locate-loop A/B on the 10-K strata.
10. Final: freeze `answer_v2` and one test-split run of the winning config (cap $3), report with per-stratum CIs and attribution.

**C. Code and content (no spend, I can do these in parallel)**
11. Author the 5 `formatting` items (multi-column, scanned page, DOCX tracked changes) and observe extraction/chunking in attribution.
12. Run the S21.09 `/security-review` and file findings in `docs/followups.md`.
13. Turn each accepted A/B arm into the default (one-line config change each) and delete rejected arms.
14. Reconcile the plan tick boxes against evidence (S17, S18, S20 and all of S21–S23).

**D. Deliverables the assignment grades**
15. README numbers filled from the final run (each with its regenerate command).
16. Demo recording (script exists: `scripts/demo_core.sh`).
17. Diagram SVG render, final fresh-clone gate (S18.12, S19.07, S20.08), cleanup of the throwaway eval users and documents in the dev DB.

## 5. Time and money to finish (estimates, not measured)
- Your time: about 2 to 3 hours (audit review, labels, UI review).
- Live spend: about $5 to $8 (A/B round, abstention calibration, final test run), against about $7 spent of the $30 cap.
- Wall clock after you finish the review: roughly one working day for A/B arms and the final run, running at most 3 lanes in parallel.
- Biggest risk: the goal of about 95% may not be reachable with these changes alone. The measured real failure rate is 27 of 123 (22%) on train, concentrated in ranking cuts and retrieval misses. If the A/Bs fall short, the honest deliverable is the measured numbers, the attribution, and the reasoned list of what would be next.
