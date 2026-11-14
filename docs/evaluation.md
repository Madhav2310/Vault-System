# Evaluation

How Vault's quality is measured, what the measurements say today, which decisions they drove, and how to run an evaluation cycle. Every number carries the command that regenerates it. Numbers marked **provisional** come from judges that are not yet calibrated against human labels, or from gold that has not been audited by a person.

Contents: [1. Method](#1-method) · [2. Current measurements](#2-current-measurements) · [3. What the failures look like](#3-what-the-failures-look-like) · [4. Decisions made from measurements](#4-decisions-made-from-measurements) · [5. Runbook](#5-runbook) · [6. Open measurement work](#6-open-measurement-work)

---

## 1. Method

### 1.1 Golden set

`eval/datasets/vault_eval_v2/` holds 316 live items over 189 documents in 15 strata, with at most 3 items per document.

| Property | Value |
|---|---|
| Strata | single_span, conditional, table_lookup, numeric_calc, multi_span, multi_hop, compare_docs, conflict, aggregation, unanswerable, false_premise, versioned, injection, multi_turn, formatting |
| Splits | By a hash of the source document, so no document spans two splits: train 123 items, dev 59, test 134 (`eval/splits_v2.py`). Train is for finding problems, dev decides behaviour changes, test is read only for the final report. Test-split content is never used for tuning. |
| Gold | Atomic claims: `required_claims` (1 to 6, each checkable on its own), `alternates`, and `gold_evidence` as half-open sentence ranges. Ranges are mapped to chunks at run time (`eval/mapping.py`), so gold survives a change of chunker. |
| Sources | Third-party labels (CUAD contracts, QASPER papers, FinanceBench filings) plus authored fixtures for the strata no public set covers (versioned, injection, conflict, false premise, multi-turn). |
| Audit | A stronger independent model reviewed every item and queued disagreements for a human (`eval/datasets/vault_eval_v2/audit_review.md`: 25 items to read, 44 to skim). Gold is **unaudited** until a person applies the decisions. |

The older set `eval/datasets/vault_eval_v1/` (122 items over 15 documents, with 86 items labelled by third-party experts) stays loadable. The frozen baselines in `eval/baselines/` were measured on it.

### 1.2 Graders and judges

- **Deterministic graders first** (`eval/graders.py`): citation integrity and in-scope checks, overlap with gold citations (chunk and sentence precision and recall), `must_not_contain`, prompt-injection violations, meta-question correctness, retrieval recall@8 and MRR@40. An item that a deterministic grader decides never reaches a judge.
- **Claim-level judges** (`eval/judge.py`, role `deep`): `eval_judge_claims` checks each required claim against the answer, `eval_judge_support` checks each answer segment against only its own cited passages, `eval_judge_abstain` checks that declining or correcting a false premise was the right behaviour.
- **Bias controls**: reference-guided (the judge sees gold), blind (model names are scrubbed), judge tier at or above the generator tier, and a hash-selected subset re-judged by the `flagship` role for inter-judge agreement. Judge and generator come from the same vendor. This is mitigated, not eliminated.
- **Calibration status**: the claim, support and abstention judges are **not** calibrated against human labels. The only measured agreement is the in-product `support_check` against the faithfulness judge: agreement 0.92, kappa 0.83, n = 37.

### 1.3 Statistics

- **Paired comparison** (`eval/paired.py`): items are paired by id, with a 10,000-resample paired bootstrap CI. Recall uses an exact McNemar test.
- **Run fingerprints** (`eval/fingerprint.py`): every v2 sidecar (`eval/reports/<run_id>.json`) carries a `fingerprint` object, so two runs are compared only when they measured the same thing. Fields: `dataset` (`id`, `split`, `gold_sha`: a content hash of the loaded live items of the split, covering `golden.yaml`, `parts/*` and `authored/*`; `n_gold`), `items` (`n`, sorted `ids`, `sha`: the items actually scored), `filter` (the `--filter` spec), `judge` (`claims`, `support`, `abstain` as `id@version` from `PROMPTS_V2`), `answer` and `code` (`git_sha`, `dirty` for tracked files). `answer` is what the server recorded, not local settings: `prompts` (union over the run's turns), `models` per role, `retrieval` and `switches` (every `chat_*` setting) come from `rag_runs.config`, and `index` (`chunker` from `document_versions.chunker_version`, `contextual_prefix`) from the ingested documents; a value that differed between turns reads `a|b`. When no turn was recorded, `answer` is `{"unavailable": true}` and the comparison refuses. The hashes are deterministic: canonical JSON, SHA-256, first 16 hex characters.
- **Strict paired comparison**: `python -m eval.paired A.json B.json --metric answer_pass` exits 2 and prints a field-by-field diff when the fingerprints differ. A field that may differ is named with `--allow <field>` (repeatable; a dotted path or its parent, for example `answer.prompts`, `retrieval`, `answer`, `judge`, `code`). Arms that evaluated different item sets need `--allow items`, which also covers `filter`. An unknown field name is refused. A sidecar without a fingerprint (a report from before fingerprints) needs `--legacy`, which warns that the basis is unchecked. `python -m eval.rejudge ... --write-sidecar` writes `<run_id>.rejudged.sidecar.json` with the new metrics and the new judge versions in its fingerprint (its `code` and `items` are those of the re-judge), so a re-judged baseline compares against a fresh run with `--allow code` only when the judged item sets match.
- **Clustered CIs and minimum detectable effect** (`eval/report.py`: `cluster_ci`, `design_effect`, `mde`): items from one document are not independent, so intervals cluster by source document. The MDE says when "no detectable difference" only reflects low power.
- **Stage attribution** (`eval/attribution.py`): every failed item gets exactly one earliest faulty stage: extraction, chunking, retrieval, ranking, utilization, citation, generation, abstention or grading. An unknown signal is skipped, never guessed. Extraction and chunking are not yet observable and always read 0.

### 1.4 Gate tiers (`eval/gate.py`)

**Tier 0**, every change, $0. `make eval-gate-t0` (needs the compose Postgres) runs: dataset schema and coverage, the document map check, citation integrity of stored runs, offline replay of the latest run, the retrieval-only comparison against the pinned retrieval baseline, and `pytest tests/integration/security`.

**Tier 1**, prompt, model or routing changes, live on a sample. Produce a candidate sidecar with `make eval-answers-v2 SPLIT=dev MAX_USD=1`, then `make eval-gate BASE=eval/baselines/answer_v2.json CAND=eval/reports/<run_id>.json` (`GATE_ARGS=--strict-power` also fails on `INCONCLUSIVE`). Exit 1 on any FAIL. The model call is never made by the gate itself.

| Criterion | Rule |
|---|---|
| `answer_pass`, `support_rate` | Non-inferior: the clustered-CI lower bound of the paired delta is above -2 points |
| `injection_violation` | Candidate sum is 0 |
| `abstention_correct` | The paired mean does not drop |
| `citation_integrity` | Candidate mean is 1.0 |
| Coverage and errors | No candidate-only items; errored items not above the baseline |

A criterion that passes while the paired MDE exceeds the margin is printed `INCONCLUSIVE`, never as success. A planted-regression test proves the gate can fail: `cd backend && uv run pytest tests/integration/eval/test_gate_tiers.py::test_planted_regression`.

### 1.5 Acceptance rule for behaviour switches

Behaviour changes ship disabled (see the Behaviour switches table in `ARCHITECTURE.md`). A switch is enabled only if a paired evaluation on the **dev split** shows either

1. the primary metric improves with the paired clustered CI excluding 0, or
2. the primary metric is non-inferior (CI lower bound above -2 points) and a secondary metric (cost or latency) improves.

The rule is fixed before the measurement. Accepting a switch is a one-line default change; a rejected switch is deleted rather than kept as dead configuration. Tuning never reads test-split content.

---

## 2. Current measurements

All rows are **provisional**: gold is unaudited and the claim-level judges are uncalibrated. Run costs are the spend recorded by the run.

| Measure | Value | Regenerate |
|---|---|---|
| Train run, 123 items, all behaviour switches off: answer_pass 0.642, claim_completeness 0.691, support_rate 0.664, citation_integrity 1.000, injection violations 0 (9 items), abstention_correct 0.600, recall@8 0.807, cite_chunk_precision 0.619, mean latency 5.2 s, $0.0057 per turn, run cost $1.23 | see left | `make eval-answers-v2 SPLIT=train MAX_USD=3`. The kept report is `eval/baselines/v2_train/01a1d89d-acd4-7586-b22e-f5c770961fea.md`. |
| The same answers re-judged with the current claim and support judges (no re-answering): answer_pass 0.659, support_rate 0.842, abstention_correct 0.840; cost $0.51 | see left | `cd backend && PYTHONPATH=.. uv run python -m eval.rejudge --answers ../eval/baselines/v2_train/01a1d89d-acd4-7586-b22e-f5c770961fea.answers.jsonl --dataset v2 --max-usd 0.6` |
| Retrieval on the legacy set, 100 items with gold: recall@8 0.759, mrr@40 0.505 (`eval/baselines/retrieval.json`) | see left | `make eval-retrieval` |
| Legacy answer baseline, train, 44 scored items: answer_pass 0.636 [0.500, 0.773], faithfulness 0.841, cite_chunk_precision 0.619, abstain_precision 0.600, mean latency 5.5 s, $0.0064 per turn (`eval/baselines/answer.json`) | see left | `make eval-answers SPLIT=train MAX_USD=3 EVAL_ARGS="--freeze answer"` |
| Lexical-only recall@8, AND query versus OR query (train, 40 items with gold) | 0.150 versus 0.633 | `EVAL_RANK=lexical make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2` |
| Fusion without rerank versus MiniLM rerank (train, 40 paired): recall@8 | 0.733 versus 0.683 (delta -0.050, CI [-0.164, +0.058]) | `make eval-retrieval-fast SPLIT=train RETR_CONFIG=retrieval@2`, then `RETR_CONFIG=retrieval@2-rerank`, then `python -m eval.paired A.json B.json --metric recall@8` |
| Same pair: mrr@40 | 0.480 versus 0.554 (delta +0.074, CI [-0.032, +0.182]) | same |
| Added rerank latency in served turns | p50 495 ms (container alone: 351 ms for 20 passages) | same, from the `retrieve` step durations |
| Whole-document versus retrieval answers, 39 paired single-document train items: answer_pass | 0.692 (retrieval) versus 0.641 (whole document), no detectable difference; cost per turn $0.0063 versus $0.0120 | `make eval-answers SPLIT=train MAX_USD=1.5 FILTER=scope_docs=1` with `FULL_CONTEXT_AUTO=true` |
| `support_check` against the faithfulness judge | agreement 0.92, kappa 0.83, n = 37, $0.14 | `cd backend && PYTHONPATH=.. uv run python -m eval.support_calibration --n 40 --max-usd 0.5` |
| Lexical variant with number and unit forms versus default, 100 legacy retrieval items: recall@8 | 0.7602 versus 0.7652 (delta -0.0050, CI [-0.0317, +0.0150], MDE 0.034) | `make eval-retrieval-fast RETR_CONFIG=retrieval@2-lexical2`, the same with `retrieval@2`, then `python -m eval.paired A.json B.json --metric recall@8` |
| Citation integrity (no unresolved citation reaches the user) | 1.000 on every kept run | `make eval-gate-t0` |
| Gold quotes locatable in the extracted PDFs | 100.0 percent | `make eval-extraction` |
| Brief facts verified by code, 3 sample documents | 56 of 58 | `python3 -c "import json;d=json.load(open('eval/baselines/brief_samples.json'));f=[x for v in d.values() for x in v['facts']];print(sum(x['status']=='verified' for x in f),len(f))"` |

Quality targets that are not met: answer_pass 0.80 (measured 0.64 to 0.66), faithfulness 0.90 (0.84), cite_chunk_precision 0.85 (0.62), time to first token p50 2.0 s (3.3 s at the last measurement). They are recorded, not relaxed.

---

## 3. What the failures look like

A root-cause pass over the 51 train items flagged by the run in section 2 (44 with answer_pass 0, plus 7 failed on abstention or `must_not_contain`) found 27 real system failures and 24 artifacts of the grader or the gold.

| Cause | Items |
|---|---:|
| Ranking cut: the gold chunk was retrieved but not kept in the evidence | 12 |
| Retrieval miss: the gold chunk is not among the candidates | 5 |
| Generation omission: a small required detail missing | 5 |
| Wrong abstain: the gold was in the evidence and the answer was empty | 3 |
| Generation error | 2 |
| Judge stricter than the claim warrants | 15 |
| Gold wrong or incomplete | 9 |

What this means for the system:

- **Ranking is the largest known lever.** Twelve of 27 real failures lose the right chunk between retrieval and the evidence set. Aggregation and compare questions silently drop one of several documents.
- **Multi-statement numeric questions** (a ratio over the income statement and the balance sheet) fail at retrieval, and the model then abstains correctly given what it saw.
- **Only 3 of 15 abstentions on answerable items** had the gold in the evidence, so abstaining was mostly the gate working as designed.

What this means for the instrument:

- The support judge penalises a segment that names a document or party that only the question or the document header supplies. About 60 percent of the sampled "unsupported" segments were this pattern, so a support rate near 0.66 must not be read as 34 percent hallucination. The re-judged value (0.842) removes part of that effect.
- The abstention judge vetoed correct answers on `versioned` items (the right behaviour there is to answer from the current version); the fix lifts abstention_correct from 0.60 towards 0.84.
- Precision metrics count an abstention as 0 for citation precision, which punishes a retrieval fault twice. Report them on answered items and report abstention separately.
- Many required claims are verbatim fragments or whole clauses, so `answer_pass` on multi_span (0.083) mostly measures how closely an answer copies the reference. Of its 11 failing items, 6 are judge or gold artifacts.

---

## 4. Decisions made from measurements

| Decision | Evidence |
|---|---|
| The lexical leg uses an OR query. | Lexical-only recall@8 rose from 0.150 (AND) to 0.633 (OR). |
| **Answer prompt with detail discipline** (`answer_rag@10`, the default). | Paired runs against `answer_rag@7`, both on `retrieval@5`: support_rate 0.858 to 0.902 (train, +4.5, clustered CI [-0.6, +9.4]) and 0.881 to 0.908 (dev, +2.7, CI [-3.8, +10.6]); answer_pass 0.864 to 0.856 (train) and 0.746 to 0.763 (dev), no detectable difference; injection 0, citation integrity 1.00, must_not_contain all passed. The rules: no label, superlative, unit, count or figure the cited sentences lack; the latest version only for current-terms questions; keep every cited detail. A contamination sweep (word 5-grams of every prompt against every item's question and claims) found `answer_rag@7`'s example sentence was a dev item's gold wording; `answer_rag@11` (`@7` with a neutral example) measured the effect on dev: 0.746 to 0.763, inside run-to-run noise. |
| **LLM listwise reranker over 50 candidates** (`retrieval@5`, the default). | Paired answer runs against `retrieval@2` (no reranker): dev answer_pass 0.661 to 0.746 (+8.5, clustered CI [0.0, +17.9], 6 gained, 1 lost), train 0.737 to 0.864 (+12.7, CI [+6.5, +19.8], 17 gained, 2 lost); support_rate -1.4 (dev) and -2.8 (train), inside the run-to-run spread of about 2.7 points measured between two runs with identical reranker input; injection 0 and citation integrity 1.00 in every run; +$0.0004 per turn. With 20 candidates the dev gain was +1.7 (not detectable): gold chunks sat at fused ranks 11-44. Collapsing dot leaders in the reranker's copy lets financial-statement rows fit the window (the Adobe current-liabilities row). The MiniLM cross-encoder stays off: recall@8 and MRR@40 showed no detectable difference and it added 495 ms at p50. |
| **Evidence gate off** (`gate.enabled: false`). | With the calibrated floor, 3 of 44 answerable train items were refused. The floor value stays in `backend/config/retrieval@2.yaml` for when scored abstention replaces it. |
| **Whole-document route not automatic** (`FULL_CONTEXT_AUTO=false`). | No gain (0.692 versus 0.641, no detectable difference) at about twice the cost per turn. The route is still available on request. |
| **Support check on, advisory** (`support_check@1`). | Agreement 0.92 and kappa 0.83 against the faithfulness judge. It annotates verdicts and never blocks an answer. |
| Number-aware lexical matching not enabled. | No detectable difference on the legacy retrieval set, which has few numeric queries; the numeric strata of the current set are the place to decide. |
| Citations are application-managed (handles, code-copied text). | A spike over 12 items measured 0 invalid handles and 15 of 15 segments cited; citation_integrity is 1.000 on every kept run. |
| PDF extraction uses `use_text_flow=True, x_tolerance_ratio=0.15`. | 99 of 100 expert gold quotes located, against 86 of 100 with the library defaults; two-column papers 22 of 23 against 9 of 23. |

---

## 5. Runbook

An ordered cycle for the golden set. Run from the repository root, one heavy command at a time (check `uptime`; wait if the load average is above about 8). Every live step has a hard `MAX_USD` cap. A run that reaches the cap exits 3 with a partial result and freezes nothing. Keys live only in the gitignored `.env`. "Decide" marks a point where a person reviews before the next step.

**0. Prerequisites ($0).**

```bash
make eval-v2-docs ARGS=--download     # fetch the source documents
make eval-v2-docs ARGS=--check        # verify the document map
```

Set `BRIEF_ENABLED=false` in `.env` so evaluation uploads do not pay for briefs. The worker reads `.env` through `env_file`; a shell export does not reach it. Start or recreate the stack (`make up`), then confirm with `docker compose exec -T worker printenv BRIEF_ENABLED`, which must print `false`. For realistic retrieval also run `make up-rerank`; without it runs are marked degraded (`no_rerank`).

**1. Train run (answers and judges).**

```bash
make eval-answers-v2 SPLIT=train MAX_USD=3
```

Writes `eval/reports/<run_id>.md`, `<run_id>.json` (metrics sidecar) and `<run_id>.answers.jsonl` (answer text, cited passages, flags; the input to steps 2 and 7). Do not freeze on train: it exists to find system and gold problems. Decide: read the per-stratum table and any degraded or errored items.

**2. Independent gold audit** (cap $2, role `flagship`).

```bash
make eval-audit ANSWERS=eval/reports/<run_id>.answers.jsonl MAX_USD=2
```

Writes `eval/datasets/vault_eval_v2/audit_queue.yaml`. Re-running refuses to overwrite a queue that already holds human decisions. A free rehearsal: `cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --answers ../$ANSWERS --dry-run --limit 5`.

**3. Human review ($0, human time).** Render the queue with `cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --render ../eval/datasets/vault_eval_v2/audit_queue.yaml --render-out ../eval/reports/audit_review.md`. Fill `decision` for every queued item in `audit_queue.yaml`: `gold_wrong`, `gold_incomplete` (add the full corrected list as `fix`), `ambiguous` (the item is dropped; add a `note`) or `system_wrong`.

**4. Apply the decisions ($0).**

```bash
make eval-audit-apply APPLY_ARGS=--check     # validate, print the summary, write nothing
make eval-audit-apply                        # surgical edits of the dataset part files
make eval-v2-docs ARGS=--check
```

It refuses (exit 2, nothing written) on a missing decision or a stale queue. Review `git diff eval/datasets/vault_eval_v2` and commit before any further run, so later runs see the audited gold.

**5. Dev run (feedback for tuning and for accepting switches).**

```bash
make eval-answers-v2 SPLIT=dev MAX_USD=3
```

No freeze. Compare candidates to the baseline with `make eval-gate` (section 1.4).

**6. Test run and baseline freeze (once).**

```bash
make eval-answers-v2 SPLIT=test MAX_USD=3 EVAL_ARGS="--freeze answer_v2"
```

Writes `eval/baselines/answer_v2.json` with the per-stratum table, clustered CIs, MDE inputs and attribution. Run it only on audited gold and a configuration you will not change before the freeze. A cheaper injection-only check: `make eval-answers-v2 SPLIT=test FILTER=stratum=injection MAX_USD=0.5`. Decide: accept or reject the baseline.

**7. Label export for judge calibration ($0).**

```bash
make eval-labels ANSWERS=eval/reports/<run_id>.answers.jsonl N=200
```

Writes `eval/labels/labeled.yaml` (about 200 pairs, about 40 percent failures, null labels, split by source document). After the human labels (see `eval/labels/README.md`), calibrate with `cd backend && PYTHONPATH=.. uv run python -m eval.judge --calibrate ../eval/labels/labeled.yaml --max-usd 3`.

Retrieval-only measurements cost cents and need no judge: `make eval-retrieval-fast SPLIT=train RETR_CONFIG=<config>`. Stored candidates can be re-ranked offline for $0: `cd backend && PYTHONPATH=.. uv run python -m eval.replay --run latest --rank rrf --by-category`. The answers-file shape is checked by `cd backend && uv run pytest tests/unit/eval/test_answers_dump.py -q`.

Budget caps per step: train $3, audit $2, dev $3, test and freeze $3, calibration $3. Record the printed `spent_usd` of each step.

---

## 6. Open measurement work

Nothing below is measured today. Each item names what closes it.

- **Gold audit by a person.** Apply the decisions in `eval/datasets/vault_eval_v2/audit_review.md` (runbook steps 3 and 4). A gold fix that only deletes claims the system fails would bias the set towards the system, so each proposal is read, not bulk-applied.
- **Judge calibration against 200 human labels** (runbook step 7), followed by kappa and true-negative-rate checks on the claim, support and abstention judges.
- **A baseline frozen on the audited test split** (`eval/baselines/answer_v2.json` does not exist yet).
- **Paired dev-split evaluations of the switches that are off** (see Behaviour switches in `ARCHITECTURE.md`): plan-first answers, calculation segments, compare quotas, support check with repair, the shorter analysis prompt, scored abstention, the locate loop, voice, the second chunker and contextual index text. The retrieval-only arms (number-aware lexical matching, chunker, deterministic index headers) cost nothing; the answer arms are a few dollars in total.
- **Stage attribution for extraction and chunking**, which currently reads 0 for lack of a signal.
- **The `formatting` stratum** has 0 of 5 items (multi-column, scanned page, tracked changes).
- **`table_lookup` gold** is model-drafted and unaudited.
- **Live time to first token** after the latency work (`make eval-smoke EVAL_ARGS=--stream`); the last measurement is 3.3 s against a 2.0 s target.
- **Prompt injection on the test split** (0 violations on the 9 train items only).
- **Per-document embedding cost, documents per hour per worker, exact search at 100,000 chunks and any concurrency figure** (see `docs/operations.md`).
