# Pass 6 — Convergence engine: tuning that converges to quality targets on its own

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.

Goal: given the golden set (`eval/datasets/vault_eval_v1`, 122 items) and the [T] targets, an automated loop improves the pipeline **stage by stage, upstream first**, accepting only changes that beat the measured noise on held-out documents. It stops when targets are met, when progress plateaus, or when the budget runs out. Method follows Anthropic's eval hill-climbing guidance (train/test split, noise floor, one change per round, revert on regression, plateau with K ≥ 3) [V: claude-api skill `shared/evals/eval-hillclimb.md`].

---

## 1. Why stage-ordered

Errors flow downstream: garbled extraction can't be fixed by a better prompt, and a missed retrieval can't be fixed by a better gate. Each stage is tuned against **the cheapest metric that isolates it**, and most stages cost little or nothing in LLM calls:

| Stage | Levers | Metric (train split) | LLM cost per trial | Search method |
|---|---|---|---|---|
| **A. Extraction** | `use_text_flow`, `x_tolerance(_ratio)`, column repair, footnote filter, dehyphenation | Gold-quote locatability; V1 round-trip rate | **0** | Grid (small space) — *already run: 86 → 99/100* [V] |
| **B. Chunking & retrieval** | chunk target/max/overlap, structural prefix on/off, k_vec, k_lex, rrf_k, id_boost, top_n, reranker model, neighbor expansion | recall@8, MRR@40 (gold quote ∈ selected chunks) | ≈ 0 (embeddings cached by content hash; re-chunk costs cents) | Bayesian (Optuna TPE [C], MIT), ~100 trials |
| **C. Gate & mode calibration** | gate HIGH / FLOOR bands, full-context threshold | Abstention F1, coverage-state accuracy (computed offline from recorded scores and one recorded LLM-gate pass) | One recording pass, then 0 | Exhaustive sweep over recorded scores |
| **D. Prompts** | `analyze_query`, `evidence_gate`, `answer_rag/full`, `brief_extract` | Faithfulness, citation precision, completeness, routing accuracy (judge) | Full LLM runs | LLM-analyzer hill-climb (one change per round) |
| **E. Model & mode** | `ANSWER_MODEL` ∈ {Sonnet 5.5, Opus 5.5}, effort, RAG vs full-context | All quality metrics + latency + cost | Full LLM runs | Ablation matrix (fixed, not searched) |

Re-entry rule: if a later stage's failure attribution (§2) shows that most remaining failures belong to an earlier stage, the engine goes back to that stage.

---

## 2. Automatic failure attribution (from the evidence ledger)

Every failed item is bucketed **mechanically**, because the ledger records candidates, selection, citations and gold:

| Bucket | Test | Owning stage |
|---|---|---|
| `extraction_miss` | gold quote not locatable in the version's extracted text | A |
| `retrieval_miss` | no gold-bearing chunk among the 80 hybrid candidates | B |
| `ranking_miss` | gold chunk among candidates but not selected after rerank | B |
| `budget_miss` | gold chunk selected but dropped by the evidence token budget | B/C |
| `gate_false_abstain` | gold chunk selected, gate abstained | C |
| `gate_false_answer` | unanswerable item answered with claims | C/D |
| `generation_miss` | gold chunk selected, judge says the answer is wrong or incomplete | D |
| `citation_miss` | answer correct but its citations don't include a gold-bearing chunk | D |
| `conflict_miss` | conflict item without the conflict flag, or a control item flagged | C/D |
| `grader_suspect` | judge confidence low, or judge and the deterministic check disagree | human queue |

The engine always works on **the stage owning the largest bucket on train** (power law). The per-bucket table is part of every round's report, which also gives the user an evidence-based answer to "where is quality lost?".

---

## 3. The loop

```
baseline (train+test, reps=2) → measure noise floor (baseline run twice; paired per-item deltas)
repeat:
  attribute failures on TRAIN → pick owning stage → propose ONE change
     A/B/C: next trial from the search algorithm (cheap metric, train only)
     D: analyzer (fresh context) reads TRAIN transcripts only and proposes one generalizable prompt change
  run → TRAIN (+ TEST for D/E rounds, scored, never read)
  accept iff:  ΔTEST(primary) > max(noise_floor, 2 pp)   and guardrails hold
               (citation integrity = 100% [A]; p50 latency ≤ +20%; cost/question ≤ +25%; no metric beyond 2 pp below baseline)
  else revert
  record round (change.md, results.jsonl, _state.json, spend)
stop when: all [T] targets met on TEST  |  plateau: < delta for K=3 consecutive rounds per stage  |  budget ceiling reached
report: baseline → winner on TEST with CIs, per-round table, failure buckets before/after
```

**Anti-overfitting:**
- **Document-level holdout:** test-only documents are `cuad_license`, `qasper_kurdish`, `fb_3m_10q` and `auth_side_letter`, so improvements must generalize to unseen documents.
- The analyzer never sees test transcripts or gold answers outside train.
- Prompt changes must describe failure *behavior*, never paste item content. A lint rejects prompt diffs containing any n-gram (n ≥ 5) from train gold answers.
- A train-up / test-flat round is reverted.

**Statistics:** 72 test items × 2 reps gives a 95% CI half-width ≈ 1/√144 ≈ ±8 pp on a binary pass rate [V: rule of thumb in the hill-climbing guide]. So decisions use **paired** per-item deltas (bootstrap CI), which are much tighter than unpaired comparisons. A change is accepted only if the paired CI excludes 0.

---

## 4. Cost controls

| Control | Effect |
|---|---|
| **Stage caching**: results keyed by `(item, stage-config hash, prompt@v, model)` | Only stages downstream of a change re-run |
| **Frozen-evidence replay** (design §10.3) for stage D | Prompt rounds skip retrieval entirely |
| **Smoke screening** (21 items) before a full train+test run | Bad ideas die cheap |
| **Message Batches API** for full runs (50% price, not subject to ramp limits [V]) | Halves cost; async (fine for a loop) |
| **Prompt caching** of system prompt and full-context evidence (cache reads don't count toward ITPM [V]) | Lower cost and higher throughput |
| **Spend ledger with hard ceiling** (from `llm_calls`) | The engine stops itself; budget is a user decision |

**Cost per full run (estimate; measured in S3.15 and replaces these numbers):** 122 items × 2 reps ≈ 250 answer turns. RAG on Sonnet 5.5 ≈ $0.02/turn plus judge ≈ $0.005/turn → **≈ $6 per full run (≈ $3 via Batch)**. Opus 5.5 ≈ 2× the generation part. The full ablation matrix (RAG/full-context × Sonnet/Opus) ≈ $25–40. A 10-round prompt loop with smoke screening ≈ $30–60.

---

**Chosen policy (user decision 2026-11-12): Balanced.**
- Sync calls for smoke and dev runs; Message Batches API for full train+test runs and the stage E ablations.
- `ANSWER_MODEL` = Sonnet 5.5 throughout tuning; Opus 5.5 appears only in the stage E ablation.
- reps = 2. Console spend limit **$200**, and the engine's `BUDGET_USD` defaults to $150 for the whole project.
- Estimated total eval spend ≈ $80–150.

## 5. Targets, with sources (the [T] values the engine converges to)

Where literature or vendor numbers exist, the target sits at the **ambitious end** of reported production values, as requested.

| Metric | Target | Basis |
|---|---|---|
| Gold-quote locatability after extraction | ≥ 99% [T] | **Measured: 99/100** with the tuned config [V] |
| Highlight round-trip (V1) | 100% fixtures [A], ≥ 99.5% corpus [T] | Construction guarantees it when I1 holds; residual for invisible/odd glyphs |
| recall@8 (gold in selected evidence) | ≥ 0.85 [T] | Reported production floor for context recall ≥ 0.85 (vendor guides list 0.70–0.85) [C] |
| Faithfulness (judge) | ≥ 0.90 [T] | Reported targets range 0.75–0.92 [C]; native citations + gate justify the top of the range |
| Citation integrity | 100% [A] | API extracts `cited_text` and guarantees valid pointers [V: citations docs] |
| Citation precision (cited sentence supports the claim) | ≥ 0.85 [T] | No public benchmark; set at the faithfulness level minus margin |
| Abstention precision and recall (expert-labelled unanswerables) | ≥ 0.85 each [T] | No standard; FinanceBench showed refusals/incorrect answers are a top failure for retrieval systems (81% of sample questions failed for a 2023 GPT-4-Turbo + retrieval baseline) [C] |
| FinanceBench slice correctness | ≥ 0.60 [T] | Hard numeric/table questions; 2023 baseline 19% correct [C]. Tracked per slice so it doesn't mask contract/paper results |
| Judge–human agreement | ≥ 0.80 [T] | LLM-judge agreement with humans >80%, on par with human–human agreement (Zheng et al. 2023, MT-Bench) [C] |
| Routing accuracy | ≥ 0.90 [T] | Small closed label set; Haiku structured output |
| Sentence segmentation | ≥ 96% on the 50-case fixture [T] | Rule-based segmenters report ≈ 98% on standard "golden rules" suites (pySBD) [C] |
| Reranker latency, 40 pairs | p95 < 100 ms on M-series [T] | Qdrant measured MiniLM-L6 ONNX at ≈ 0.5–1.7 ms per pair on an M4 Max [C] → 20–70 ms |
| RAG TTFT p95 | ≤ 3 s [T]; full-context ≤ 8 s [T] | Estimated budget (research 04); measured in S5.14 |

---

## 6. Deliverables (plan tasks)
- `eval/engine/`: runner with stage caching, Batch mode, spend ledger, paired-bootstrap statistics, failure attribution, state directory, reporting.
- Stage A/B/C optimizers (S2 / S4), stage D hill-climb (S4), stage E ablations (S4.17).
- `make tune STAGE=<a|b|c|d> BUDGET_USD=<n>` and `make tune-all`.
- Grafana D5 shows the per-round series.

## Sources
- Claude API skill: `shared/evals/eval-hillclimb.md`, `build-eval.md` (methodology)
- [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits) (cache-aware ITPM, ramp limits, Batches limits, spend caps)
- [RAGAS metric targets — premai](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026) · [markaicode](https://www.markaicode.com/rag-evaluation-ragas-metrics-production/) · [dev.to](https://dev.to/anna_danilec/rag-evaluation-with-ragas-measuring-faithfulness-context-precision-and-recall-in-production-31hj)
- [FinanceBench paper](https://www.emergentmind.com/papers/2311.11944) · [FinanceBench repo](https://github.com/AI-App/Patronus-AI.FinanceBench)
- [Qdrant — cross-encoder latency](https://qdrant.tech/blog/oxidizing-cross-encoders/)
- Zheng et al. 2023, "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (agreement figures)
