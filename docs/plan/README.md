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
Balanced (user decision 2026-11-12; re-based on OpenAI 2026-11-13, research 06 §4): sync for smoke/dev runs, OpenAI Batch API (50% off) for full runs and ablations; `balanced` generator, `deep` judge, `flagship` only on the judge-calibration subset and in stage E; reps = 2; engine `BUDGET_USD` default **$30**, OpenAI project limit **$50** (user decision 2026-11-13, lowered from $150/$200): screen on smoke, at most 2 finalists go to full runs.

**Cost discipline (user instruction 2026-11-12):** validate on samples, never on full datasets. Every live test, spike and eval command takes a sample size and a `--max-usd` cap, and the default cap is small. Full runs happen only inside the convergence engine under its budget.

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
2. **Core path → Milestone M0** (below): the 51 tasks that make upload → ingest → retrieve → cited chat → metrics work end to end, in ID order within each checkpoint
3. The rest of **S1** → **S2** → **S3** → **S4** in ID order (S4 ends with the convergence run, S4.20)
3. **Milestone M1 — spec-complete backend** (below)
4. **S5**
5. **S6 ∥ S7** (parallel worktrees allowed)
6. **S9.01–S9.13** (Tier A screens)
7. **S8** (Tier B backend: matrix)
8. **S9.14–S9.16** (Tier B screens, S9 gate)
9. **S10** (starts with the user's golden-set review S10.01 and the post-review re-evaluation S10.02)

### Plan v3 — plan of record from 2026-11-13 (user decision, after the research in `reports/Vault rebuild strategy research.md`)
**Read `docs/plan/v3.md` first** (execution guide: rules, commands, root-cause notes, gotchas). Order: **S16 → S17 → S18 → S19 → S20**. Unfinished tasks of S11, S13, S14 (S14.02, S14.04, S14.06 AC4-5, S14.07) and S15 are superseded by v3 tasks (each superseded AC names its replacement). S12 leftovers (live ACs) are run inside S17.

### Plan v2 — superseded by v3 (kept for history) from 2026-11-13 (user decision)
After the core spine (M0 minus S3.15), a critical reassessment (`docs/plan/v2.md`) re-prioritised the remaining work toward what the spec grades. **S11–S15 are now the plan of record, in this order: S11 ∥ S12 → S13 ∥ S14 → S15.** The tasks of S1–S10 that are not yet done are **deferred**, not deleted. A v2 task that names "Supersedes" replaces the old one. The rules of the per-slice workflow (CLAUDE.md) apply unchanged.

| Slice | Theme | Gate |
|---|---|---|
| S11 | Retrieval quality, latency, abstention | S11.16 |
| S12 | Document intelligence (cited brief, summaries) | S12.09 |
| S13 | Chat experience (SSE, follow-ups, meta, compare, grounding baseline) | S13.11 |
| S14 | Production readiness (limits, quotas, versions, live status, feedback, OpenAPI) | S14.08 |
| S15 | Delivery (UI, README, AI_USAGE, demo, fresh clone) | S15.12 |

Parallel lanes: S11 (main session) ∥ S12 (lane); after S11.09 merges: S13 (main) ∥ S14 (lane); S15.01–S15.05 (lane) start when S13.01 and S12.05 merge; S15.06–S15.12 last.

### Core path and Milestone M0 — working spine (user decision 2026-11-13)
Why: after S0 nothing the spec asks for existed yet, and the spec prefers "a fully functional subset over partially implemented features". The core path builds the whole spec loop first, on a strong base (failure handling, recovery, measurement), and defers depth (geometry highlight, scale, tuning stages, streaming, load) to after M0. Nothing is deleted; deferred tasks run afterwards in plan order.

| Checkpoint | Core tasks | Exit check (stop and report to the user) |
|---|---|---|
| C1 ingestion | S1.01 S1.02 S1.03 S1.04 S1.05 S1.07 S1.08 S1.09 S1.10 S1.12 S1.13 S1.15 S1.16 S1.17 S1.18 S1.19 S1.21 S1.22 S1.24 S1.25 S1.26 S1.27 | every corpus document reaches `ready` (or a correct terminal state) through the API; `make ci && make test` green; review pass |
| C2 retrieval and measurement | S2.01 S2.02 S2.03 S2.05 S2.06 S2.07 S2.08 S2.09 S2.10 S2.12 S2.13 S2.14 S2.17 S2.18 | `make eval-retrieval` report with recall@k; judges validated on a sample; spend under the cap; review pass |
| C3 chat and metrics (= M0) | S3.01 S3.02 S3.03 S3.04 S3.05 S3.06 S3.07 S3.08 S3.09 S3.10 S3.11 S3.15 S7.01 S7.02 S7.03 | `scripts/demo_core.sh` (upload → ready → ask → cited answer → history → metrics) exits 0; sampled grounding baseline; review pass |

Rules for the core path:
- **Dependency overrides**: a dependency on a slice gate (S1.32, S2.21) or on S5.16 is satisfied by the core tasks of the earlier checkpoints plus green `make ci && make test`. All other dependencies of core tasks are themselves core tasks (checked with the closure script recorded in `docs/progress.md`).
- **ACs that need deferred tasks stay open** and are listed in the checkpoint report (e.g. S3.15 AC5 highlight precision needs S1.28–S1.30). A task with such an AC is "core-done", not done, and its checkbox stays empty until the AC passes.
- **Review pass at every checkpoint**: a code review of the diff since the previous checkpoint before reporting (the post-S0 self-review found three bugs that the tests missed, F-023).
- Live calls stay sampled and capped (`--max-usd`), within the $30 engine budget.

### Milestone M1 — spec-complete backend
Reached after S4, before S5. Guarantees a submittable result even if later work is cut. M1 requires:
- [ ] S0–S4 gates passed
- [ ] S7.01, S7.02, S7.03 (the spec's metrics endpoints) done, pulled forward from S7
- [ ] README draft: setup, architecture summary, API overview, pre-review eval numbers with commands — `t:unit/docs/test_readme.py::test_m1_sections`
- [ ] AI_USAGE.md has entries for S0–S4 — `t:unit/docs/test_ai_usage.py::test_m1_entries`
- [ ] Demo script for the 5-step story runs against the API (no UI) — `$ scripts/demo_api.sh` exits 0

## Progress checklist

Tick a box only when the task's definition of done is met.

**S0** — [x] S0.01 [x] S0.02 [x] S0.03 [x] S0.04 [x] S0.05 [x] S0.06 [x] S0.07 [x] S0.08 [x] S0.09 [x] S0.10 [x] S0.11 [ ] S0.12 [x] S0.13 [x] S0.14 [x] S0.15 [x] S0.16 [x] S0.17 [x] S0.18 [ ] S0.19 [ ] S0.20 [x] S0.21 [x] S0.22 [x] S0.23 [x] S0.24 [x] S0.25 [x] S0.26 (gate)

**S1** — [x] S1.01 [x] S1.02 [x] S1.03 [x] S1.04 [x] S1.05 [ ] S1.06 [x] S1.07 [x] S1.08 [x] S1.09 [x] S1.10 [ ] S1.11 [x] S1.12 [x] S1.13 [ ] S1.14 [x] S1.15 [x] S1.16 [x] S1.17 [x] S1.18 [ ] S1.19 [ ] S1.20 [x] S1.21 [x] S1.22 [ ] S1.23 [x] S1.24 [x] S1.25 [x] S1.26 [x] S1.27 [ ] S1.28 [ ] S1.29 [ ] S1.30 [ ] S1.31 [ ] S1.32 (gate)

**S2** — [x] S2.01 [x] S2.02 [x] S2.03 [ ] S2.04 [ ] S2.05 [x] S2.06 [ ] S2.07 [x] S2.08 [x] S2.09 [ ] S2.10 [ ] S2.11 [ ] S2.12 [ ] S2.13 [ ] S2.14 [ ] S2.15 [ ] S2.16 [ ] S2.17 [ ] S2.18 [ ] S2.19 [ ] S2.20 [ ] S2.21 (gate)

**S3** — [x] S3.01 [ ] S3.02 [x] S3.03 [x] S3.04 [ ] S3.05 [x] S3.06 [x] S3.07 [ ] S3.08 [x] S3.09 [ ] S3.10 [ ] S3.11 [ ] S3.12 [ ] S3.13 [ ] S3.14 [ ] S3.15 [ ] S3.16 [ ] S3.17 (gate)

**S4** — [ ] S4.01 [ ] S4.02 [ ] S4.03 [ ] S4.04 [ ] S4.05 [ ] S4.06 [ ] S4.07 [ ] S4.08 [ ] S4.09 [ ] S4.10 [ ] S4.11 [ ] S4.12 [ ] S4.13 [ ] S4.14 [ ] S4.15 [ ] S4.16 [ ] S4.17 [ ] S4.18 [ ] S4.19 [ ] S4.20 [ ] S4.21 (gate)

**S5** — [ ] S5.01 [ ] S5.02 [ ] S5.03 [ ] S5.04 [ ] S5.05 [ ] S5.06 [ ] S5.07 [ ] S5.08 [ ] S5.09 [ ] S5.10 [ ] S5.11 [ ] S5.12 [ ] S5.13 [ ] S5.14 [ ] S5.15 [ ] S5.16 (gate)

**S6** — [ ] S6.01 [ ] S6.02 [ ] S6.03 [ ] S6.04 [ ] S6.05 [ ] S6.06 [ ] S6.07 [ ] S6.08 [ ] S6.09 [ ] S6.10 [ ] S6.11 [ ] S6.12 [ ] S6.13 (gate)

**S7** — [x] S7.01 [x] S7.02 [x] S7.03 [ ] S7.04 [ ] S7.05 [ ] S7.06 [ ] S7.07 [ ] S7.08 [ ] S7.09 [ ] S7.10 [ ] S7.11 [ ] S7.12 [ ] S7.13 [ ] S7.14 (gate)

**S8** — [ ] S8.01 [ ] S8.02 [ ] S8.03 [ ] S8.04 [ ] S8.05 [ ] S8.06 [ ] S8.07 (gate)

**S9** — [ ] S9.01 [ ] S9.02 [ ] S9.03 [ ] S9.04 [ ] S9.05 [ ] S9.06 [ ] S9.07 [ ] S9.08 [ ] S9.09 [ ] S9.10 [ ] S9.11 [ ] S9.12 [ ] S9.13 [ ] S9.14 [ ] S9.15 [ ] S9.16 (gate)

**S10** — [ ] S10.01 [ ] S10.02 [ ] S10.03 [ ] S10.04 [ ] S10.05 [ ] S10.06 [ ] S10.07 [ ] S10.08 [ ] S10.09 [ ] S10.10 [ ] S10.11 (gate)

**S11** — [ ] S11.01 [ ] S11.02 [ ] S11.03 [ ] S11.04 [ ] S11.05 [ ] S11.06 [ ] S11.07 [ ] S11.08 [ ] S11.09 [ ] S11.10 [ ] S11.11 [ ] S11.12 [ ] S11.13 [ ] S11.14 [ ] S11.15 [ ] S11.16 (gate)

**S12** — [ ] S12.01 [ ] S12.02 [ ] S12.03 [ ] S12.04 [ ] S12.05 [ ] S12.06 [ ] S12.07 [x] S12.08 [ ] S12.09 (gate)

**S13** — [ ] S13.01 [ ] S13.02 [ ] S13.03 [ ] S13.04 [ ] S13.05 [ ] S13.06 [ ] S13.07 [ ] S13.08 [ ] S13.09 [ ] S13.10 [ ] S13.11 (gate)

**S14** — [x] S14.01 [ ] S14.02 [x] S14.03 [ ] S14.04 [x] S14.05 [ ] S14.06 [ ] S14.07 [ ] S14.08 (gate)

**S15** — [ ] S15.01 [ ] S15.02 [ ] S15.03 [ ] S15.04 [ ] S15.05 [ ] S15.06 [ ] S15.07 [ ] S15.08 [ ] S15.09 [ ] S15.10 [ ] S15.11 [ ] S15.12

**S16** — [ ] S16.01 [x] S16.02 [x] S16.03 [x] S16.04 [x] S16.05 [ ] S16.06 [ ] S16.08 [ ] S16.07 (gate)

**S17** — [x] S17.01 [ ] S17.02 [ ] S17.03 [ ] S17.04 [ ] S17.05 [ ] S17.06 [ ] S17.07 [ ] S17.08 [ ] S17.09 [ ] S17.10 [ ] S17.11 [x] S17.12 [ ] S17.14 [ ] S17.13 (gate)

**S18** — [ ] S18.01 [ ] S18.02 [ ] S18.03 [ ] S18.04 [ ] S18.05 [ ] S18.06 [ ] S18.07 [ ] S18.08 [ ] S18.09 [ ] S18.10 [ ] S18.11 [ ] S18.12 (gate)

**S19** — [ ] S19.01 [ ] S19.02 [ ] S19.03 [ ] S19.04 [ ] S19.05 [ ] S19.06 [ ] S19.07 (gate)

**S20** — [ ] S20.01 [ ] S20.02 [ ] S20.03 [ ] S20.04 [ ] S20.05 [ ] S20.06 [ ] S20.07 [ ] S20.08
