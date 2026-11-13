# Vault: status report (2026-11-13, evening)

One page. Numbers are measured; each has a command in the "Evidence" column or in `docs/progress.md`.

## 1. Where we are

Plan v2 (what the spec grades) is **16% done by acceptance criteria (41 of 257)**. The foundation underneath it is built and working.

| Pillar (spec) | Done | Level of success | Evidence |
|---|---|---|---|
| Upload and async ingestion (PDF, DOCX, TXT, MD) | 95% | Strong. 19/19 corpus documents reach ready. Retries, leases, dedupe, versions, delete/purge hardened by two independent reviews. | `make c1-check` |
| Document chat with citations | 80% | Works. Citations are copied by code, so integrity is 1.00. Answers take about 6 s and don't stream yet. | `scripts/demo_core.sh` |
| Multi-turn context | 70% | Sessions, memory window and history work. The rolling summary is not built. | tests |
| **AI insights (the brief)** | **60%** | Works end to end. 56 of 58 facts verified (97%), category correct 3/3, $0.043 for 3 documents. Custom summaries and brief metrics are being built. | live sample, `docs/evidence/brief_samples.json` |
| Metrics APIs | 85% | Documents, processing and chat endpoints work. TTFT is empty and there is no node-level latency yet. | `/v1/metrics/*` |
| Evaluation | 80% | Built: 122-item golden set, deterministic graders, judges (agreement 1.00), engine, replay, fast retrieval eval. Answer quality is not yet baselined. | `make eval-retrieval-fast` |
| Streaming, UI, README, AI_USAGE, demo | 5% | README is 5 lines, AI_USAGE is a stub, no UI, no recording. | |
| Production extras (rate limits, quotas, versions, feedback) | 10% | Being built by a background agent. | |

Code: about 12.9k lines of app, 19.7k of tests and eval. 116 commits. Gates on main: `make ci` 286 passed, `make test` 563 passed.

**Spend so far: about $3** of the $50 limit (dev ledger $0.90, plus judges $1.11 and brief samples $0.04 outside it, plus earlier spikes). Plan v2 should cost under $10 more.

## 2. Quality measured today

| Metric | Now | Target | Status |
|---|---:|---:|---|
| Retrieval recall@8 | 0.74 | at least 0.85 | not met |
| Citation integrity (no invented citations) | 1.00 | 1.00 | met |
| Abstention precision (smoke) | 0.50 | at least 0.80 | not met |
| Answer latency p50 | 5.9 s | at most 3.5 s | not met |
| Brief facts verified | 97% | at least 80% | met |
| Judge inter-agreement | 1.00 | at least 0.80 | met |
| Faithfulness, citation precision, completeness | not measured | 0.90 / 0.85 / 0.75 | blocked on the answer baseline (S13.10) |

Recall by question type: lookup 0.82, identifier 0.67, conflict 0.58, compare 0.55, table 0.40.

## 3. Blockers and risks

1. **Reranker is unusable on this machine.** It needs 1.3 to 2.6 s for 40 passages against an 800 ms timeout, so it silently degraded in 79% of eval runs. Earlier "recall" numbers mostly measured plain fusion. Decision pending (S11.05): none, bigger model, or an LLM rerank.
2. **Machine load.** Parallel agents plus the reranker overheated the laptop. Now mitigated: CPU caps, Postgres fsync off, one heavy job at a time. It limits how many agents run in parallel (two).
3. **Gold labels are unreviewed.** Items are marked `draft`; the golden-set review is deferred to the end by your choice. Every quality number is "pre-review".
4. **Same-vendor judges.** The judges and the generator are both OpenAI. This is mitigated (deterministic graders first, reference-guided judging, tier rule) but not eliminated.
5. **Ingest worker memory.** The AMD 10-K was killed by the 1 GB limit; raised to 2 GB. Very large PDFs remain a risk (follow-up F-038, no page cap).
6. **Cancelled turns** stay `running` until the lease expires (reaper planned, S11.12).

No blocker needs you right now. Decisions I will bring you: the reranker choice, and the recall target if 0.85 proves out of reach.

## 4. Next main tasks, in order

| # | Task | Why it matters | Owner |
|---|---|---|---|
| 1 | Finish retrieval fixes: lexical search now matches on any word (done, measuring), identifier tokens (S11.04), reranker decision (S11.05), `retrieval@2` config (S11.06) | Reaches the recall target | main |
| 2 | Latency: skip question analysis on first questions, run it in parallel otherwise (S11.08, S11.09) | About 2 s off every answer | main |
| 3 | Evidence check before answering (S11.10) | Fixes wrong "I don't know" answers | main |
| 4 | Custom summaries, brief eval, brief metrics (S12.06 to S12.08) | Completes AI insights | agent, in progress |
| 5 | Streaming answers, follow-ups, meta questions, compare, conflicts (S13) | The most visible chat quality | main |
| 6 | Rate limits and quotas, new document versions, live status, feedback (S14) | Production-readiness bonuses | agent, in progress |
| 7 | Answer-quality baseline with judges (S13.10), capped at $3 | Proves faithfulness | main |
| 8 | UI, README, diagrams, AI_USAGE, demo (S15) | Graded directly | agent plus main |

Parallel plan: S11 (main) with S12 and S14 (agents) now; S13 after S11.09; S15 last. Estimated remaining work: S11 about 60 more criteria, S12 17, S13 54, S14 39, S15 40.

## 5. Optimizations queued

- **Latency (5.9 s to 3.5 s):** drop the separate analysis call on first questions (about 2 s); run retrieval in parallel with analysis; stream the answer so first text arrives in about 2 s even if the whole takes longer.
- **Cost per answer ($0.0056 now):** cache prefix is already explicit; a cheaper rerank option; skip the analysis model call on the fast path.
- **Retrieval:** identifier index, any-word keyword matching (done), embedding-prefix A/B (S11.07), per-document retrieval for comparisons.
- **Cost control:** daily per-user quota (S14.02), per-document brief cap ($0.05), eval spend caps on every live command.
- **Machine:** run replay and train-split evals instead of full runs; the reranker stays off except for its bake-off.
- **Deleted from scope** (low visibility for the cost): convergence-engine tuning stages, geometry vision QA, DB-scale simulation, comparison matrix, most of S4 to S8 and S10.

## 6. How to verify any claim here

- Gates: `make ci && make test`; corpus ingestion: `make c1-check`.
- Retrieval: `make eval-retrieval-fast` (needs the stack up); rankings: `python -m eval.replay`.
- End-to-end demo: `scripts/demo_core.sh`.
- Plan progress: `python3 scripts/plan_lint.py` and the checklists in `docs/plan/S11.md` to `S15.md`.
- Log: `docs/progress.md`; open issues: `docs/followups.md`; handoff: `docs/HANDOFF.md`.
