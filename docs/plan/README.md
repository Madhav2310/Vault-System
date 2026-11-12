# Execution plan

This folder **is the plan.** `docs/design.md` says *what* to build; these files say *in what order*, *in what atomic steps*, and *how each step is proven done*.

| File | Content |
|---|---|
| `README.md` | Conventions, threshold policy, slice order, progress checklist |
| `S0.md` … `S10.md` | Atomic tasks with acceptance criteria |
| `stretch.md` | Eval-gated optional work (not required for completion) |
| `coverage.md` | Traceability: every design section and every spec requirement → task IDs (reviewed twice) |

Consistency check: `python3 scripts/plan_lint.py` must exit 0 after any edit to these files.

---

## Conventions

### Task format
```
### S1.02 — Title                                    (status in the checkbox list below)
Deps: S1.01 · Design: §6.1 · Files: app/documents/sniff.py
Do: one or two sentences of scope.
- [ ] AC1 <one observable fact> — <verification>
- [ ] AC2 …
Evidence: what to paste into docs/progress.md
```
- **Atomic task**: one cohesive change, reviewable on its own, usually ≤ ~300 changed lines. If a task grows past that, split it and give the new tasks IDs like `S1.12a` / `S1.12b`.
- **Atomic AC**: checks **exactly one** observable fact, verified by **exactly one** of:
  - `t:` a pytest node ID under `backend/tests/` (e.g. `t:unit/documents/test_sniff.py::test_allowed_types`). The test is written as part of the task, and its name is part of the contract.
  - `$` a shell command plus its expected outcome.
  - `M` a manual check, **only** where automation is impossible (human judgment, recordings). Each M names who performs it.
- **Owner tags**: tasks are Claude's unless marked **[USER]**.
- **Optional tasks** are marked **[OPT]**. They're still checklisted, but don't block the slice gate.

### Threshold policy
- **[A] absolute**: correctness invariants, e.g. citation integrity = 100%, zero duplicate rows, cross-user access → 404. Never relaxed.
- **[T] target**: a numeric target chosen now from research estimates, e.g. scoped vector query p95 < 50 ms. If a [T] isn't met, record the measured value, stop, and ask the user. **A [T] value may only change with user approval, logged in `docs/progress.md`.**
- **[R] relative**: comparisons where no absolute number is known in advance, e.g. "hybrid recall@8 ≥ vector-only recall@8", or "≤ 2 pp below frozen baseline".
- **Baselines**: the first passing run of an eval metric is frozen to `eval/baselines/<metric>.json` (git-committed). Later tasks compare against it. Re-baselining needs user approval.
- No AC may say "report X". Every AC states a pass condition.

### Definition of done (per task)
1. All its ACs are checked.
2. `make lint typecheck test` passes.
3. A `docs/progress.md` entry exists: task ID, commit SHA, commands run, real output summary, anything left UNVERIFIED.
4. `AI_USAGE.md` is updated if Claude-assisted work involved corrections or prompt iterations.
5. One commit per task, with the message prefixed by the task ID (`S1.02: magic-byte sniffing`).

### Definition of done (per slice)
Every non-[OPT] task is done, and the slice's **Gate** task (last task in each file) is done.

### Eval spend policy
Balanced (user decision 2026-11-12, research 06 §4): sync for smoke/dev runs, Batch API for full runs and ablations, Sonnet 5.5 for tuning, Opus 5.5 only in the stage E ablation, reps = 2, Console spend limit $200, engine `BUDGET_USD` default $150.

### Slice rules
- Slices run in order. A slice starts only after the previous gate passes.
- S6 and S7 may run in parallel worktrees after S5. S7.01–S7.03 are pulled forward into milestone M1.
- Tier A product features (design §2) must be done (S1–S5 plus S9 Tier A tasks) before any Tier B task (S8; S9.14–S9.15) starts.
- At the **start of each slice**, re-read its file and the referenced design sections. If measurements from earlier slices change a [T] or a task's shape, propose the edit to the user **before** coding.

---

## Slice order

| Slice | Theme | Depends on |
|---|---|---|
| S0 | Skeleton, contracts, telemetry baseline, eval corpus | — |
| S1 | Ingestion with geometry | S0 |
| S2 | Retrieval, reranker service, eval harness, DB scale | S1 |
| S3 | Grounded chat (non-streaming), evidence ledger | S2 |
| S4 | Harness, routing, gate, full-context, explainability | S3 |
| S5 | Streaming, rate limiting, resilience, load & chaos | S4 |
| S6 ∥ | Document intelligence & living answers | S5 |
| S7 ∥ | Metrics API, dashboards, alerts | S5 |
| S8 | Compare Matrix (Tier B) | S6, S7, S9 Tier A |
| S9 | Frontend | S5 (Tier A screens), S6 (brief/feedback screens), S8 (matrix screen) |
| S10 | Docs, demo, final verification | all |

---

## Execution sequence (authoritative order)

1. **S0** (incl. de-risking spikes S0.19–S0.22; if a spike fails, stop and revise the design before S1)
2. **S1** → **S2** → **S3** → **S4** (S4 ends with the convergence run, S4.19)
3. **Milestone M1 — spec-complete backend** (below)
4. **S5**
5. **S6 ∥ S7** (parallel worktrees allowed)
6. **S9.01–S9.13** (Tier A screens)
7. **S8** (Tier B backend: matrix)
8. **S9.14–S9.16** (Tier B screens, S9 gate)
9. **S10** (starts with the user's golden-set review S10.01 and the post-review re-evaluation S10.02)

### Milestone M1 — spec-complete backend
Reached after S4, before S5. Guarantees a submittable result even if later work is cut. M1 requires:
- [ ] S0–S4 gates passed
- [ ] S7.01, S7.02, S7.03 (the spec's metrics endpoints) done, pulled forward from S7
- [ ] README draft: setup, architecture summary, API overview, pre-review eval numbers with commands — `t:unit/docs/test_readme.py::test_m1_sections`
- [ ] AI_USAGE.md has entries for S0–S4 — `t:unit/docs/test_ai_usage.py::test_m1_entries`
- [ ] Demo script for the 5-step story runs against the API (no UI) — `$ scripts/demo_api.sh` exits 0

## Progress checklist

Tick a box only when the task's definition of done is met.

**S0** — [x] S0.01 [x] S0.02 [x] S0.03 [x] S0.04 [x] S0.05 [x] S0.06 [x] S0.07 [x] S0.08 [x] S0.09 [x] S0.10 [x] S0.11 [ ] S0.12 [ ] S0.13 [x] S0.14 [ ] S0.15 [x] S0.16 [ ] S0.17 [ ] S0.18 [ ] S0.19 [ ] S0.20 [x] S0.21 [x] S0.22 [ ] S0.23 (gate)

**S1** — [ ] S1.01 [ ] S1.02 [ ] S1.03 [ ] S1.04 [ ] S1.05 [ ] S1.06 [ ] S1.07 [ ] S1.08 [ ] S1.09 [ ] S1.10 [ ] S1.11 [ ] S1.12 [ ] S1.13 [ ] S1.14 [ ] S1.15 [ ] S1.16 [ ] S1.17 [ ] S1.18 [ ] S1.19 [ ] S1.20 [ ] S1.21 [ ] S1.22 [ ] S1.23 [ ] S1.24 [ ] S1.25 [ ] S1.26 [ ] S1.27 [ ] S1.28 [ ] S1.29 [ ] S1.30 [ ] S1.31 [ ] S1.32 (gate)

**S2** — [ ] S2.01 [ ] S2.02 [ ] S2.03 [ ] S2.04 [ ] S2.05 [ ] S2.06 [ ] S2.07 [ ] S2.08 [ ] S2.09 [ ] S2.10 [ ] S2.11 [ ] S2.12 [ ] S2.13 [ ] S2.14 [ ] S2.15 [ ] S2.16 [ ] S2.17 [ ] S2.18 [ ] S2.19 [ ] S2.20 [ ] S2.21 (gate)

**S3** — [ ] S3.01 [ ] S3.02 [ ] S3.03 [ ] S3.04 [ ] S3.05 [ ] S3.06 [ ] S3.07 [ ] S3.08 [ ] S3.09 [ ] S3.10 [ ] S3.11 [ ] S3.12 [ ] S3.13 [ ] S3.14 [ ] S3.15 [ ] S3.16 [ ] S3.17 (gate)

**S4** — [ ] S4.01 [ ] S4.02 [ ] S4.03 [ ] S4.04 [ ] S4.05 [ ] S4.06 [ ] S4.07 [ ] S4.08 [ ] S4.09 [ ] S4.10 [ ] S4.11 [ ] S4.12 [ ] S4.13 [ ] S4.14 [ ] S4.15 [ ] S4.16 [ ] S4.17 [ ] S4.18 [ ] S4.19 [ ] S4.20 (gate)

**S5** — [ ] S5.01 [ ] S5.02 [ ] S5.03 [ ] S5.04 [ ] S5.05 [ ] S5.06 [ ] S5.07 [ ] S5.08 [ ] S5.09 [ ] S5.10 [ ] S5.11 [ ] S5.12 [ ] S5.13 [ ] S5.14 [ ] S5.15 [ ] S5.16 (gate)

**S6** — [ ] S6.01 [ ] S6.02 [ ] S6.03 [ ] S6.04 [ ] S6.05 [ ] S6.06 [ ] S6.07 [ ] S6.08 [ ] S6.09 [ ] S6.10 [ ] S6.11 [ ] S6.12 [ ] S6.13 (gate)

**S7** — [ ] S7.01 [ ] S7.02 [ ] S7.03 [ ] S7.04 [ ] S7.05 [ ] S7.06 [ ] S7.07 [ ] S7.08 [ ] S7.09 [ ] S7.10 [ ] S7.11 [ ] S7.12 [ ] S7.13 [ ] S7.14 (gate)

**S8** — [ ] S8.01 [ ] S8.02 [ ] S8.03 [ ] S8.04 [ ] S8.05 [ ] S8.06 [ ] S8.07 (gate)

**S9** — [ ] S9.01 [ ] S9.02 [ ] S9.03 [ ] S9.04 [ ] S9.05 [ ] S9.06 [ ] S9.07 [ ] S9.08 [ ] S9.09 [ ] S9.10 [ ] S9.11 [ ] S9.12 [ ] S9.13 [ ] S9.14 [ ] S9.15 [ ] S9.16 (gate)

**S10** — [ ] S10.01 [ ] S10.02 [ ] S10.03 [ ] S10.04 [ ] S10.05 [ ] S10.06 [ ] S10.07 [ ] S10.08 [ ] S10.09 [ ] S10.10 [ ] S10.11 (gate)
