# Documentation index

Entry points: [`../README.md`](../README.md) (run it), [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (how it works), [`design.md`](design.md) (source of truth).

## Core
- [`design.md`](design.md): design, source of truth for behaviour and data model.
- [`voice.md`](voice.md): how Vault talks (chat voice and code-written messages).
- [`spec/case-study.pdf`](spec/case-study.pdf): the assignment.

## Plan (`plan/`, parsed by `scripts/plan_lint.py`)
- [`plan/README.md`](plan/README.md): checklist, definition of done, core path.
- [`plan/v3.md`](plan/v3.md): execution guide for plan v3 (S16-S20).
- `plan/S0.md` ... `plan/S23.md`: slices with atomic acceptance criteria; `coverage.md`, `stretch.md`, `v2.md` support them.

## Status (`status/`)
- [`status/STATUS.md`](status/STATUS.md): one-page measured status.
- [`status/TOTAL_PROGRESS.md`](status/TOTAL_PROGRESS.md): completion by area and what is left.
- [`status/progress.md`](status/progress.md): per-task evidence log.
- [`status/followups.md`](status/followups.md): open shortfalls and deferred work.
- [`status/SPEC_COVERAGE.md`](status/SPEC_COVERAGE.md): case-study requirement coverage.

## Evaluation (`eval/`)
- [`docs/eval/eval_gates.md`](eval/eval_gates.md): gate tiers and rules.
- [`docs/eval/eval_v2_runbook.md`](eval/eval_v2_runbook.md): how to run the v2 golden-set workflow.

## Operations (`ops/`)
- [`ops/scale.md`](ops/scale.md): scale, measured numbers and cost table.
- [`ops/dev-environment.md`](ops/dev-environment.md): Docker limits and which target needs what.
- [`ops/demo.md`](ops/demo.md): demo narration script.

## Research (`research/`)
- `research/01-...md` to `06-...md`: user research, element rethink, observability, scale and idempotency, geometry chain, convergence engine.
- `research/reports/`: the two long research reports behind plan v3 and v4.
- `research/notes/`: per-topic notes feeding those reports.

## Evidence (`evidence/`)
- Spikes (`spike_*.md`), A/B write-ups (`answer_ab.md`, `retrieval_ab.md`, `s22_ab.md`), `reranker_decision.md`, `train_v2_rca.md`.
- `runs/`: kept eval run outputs (the first v2 train run and referenced baselines). Other run outputs are gitignored in `eval/reports/`.

## Other
- `api/`: generated Postman collection. `img/`: Mermaid diagram sources.
