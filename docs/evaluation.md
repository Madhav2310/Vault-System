# Evaluation

How Vault's quality is measured, what the measurements say, which decisions they drove, and how to run an evaluation cycle. Every number carries the command that regenerates it. Judged numbers are **provisional**: the judges are not calibrated against human labels, and the test gold is an unaudited draft.

Contents: [1. Method](#1-method) · [2. Results](#2-results) · [3. Where the test run fails](#3-where-the-test-run-fails) · [4. Decisions made from measurements](#4-decisions-made-from-measurements) · [5. Runbook](#5-runbook) · [6. Open measurement work](#6-open-measurement-work)

---

## 1. Method

### 1.1 Golden set and gold status

`eval/datasets/vault_eval/` holds 311 live items over 186 source documents in 15 strata, with at most 3 items per document. Count them per split (live items, source documents): `cd backend && PYTHONPATH=.. uv run python -c "from eval.loader import load_dataset; from eval.splits import split_counts; print(split_counts(load_dataset().items))"`.

| Property | Value |
|---|---|
| Strata | single_span, conditional, table_lookup, numeric_calc, multi_span, multi_hop, compare_docs, conflict, aggregation, unanswerable, false_premise, versioned, injection, multi_turn, formatting |
| Splits | By a hash of the source document, so no document spans two splits: train 118 live items, dev 59, test 134 (`eval/splits.py`). Train is for finding problems, dev decides behaviour changes, test is run once for the final report. Test-split content is never used for tuning. |
| Gold | Atomic claims: `required_claims` (1 to 6, each checkable on its own), `alternates`, and `gold_evidence` as half-open sentence ranges. Ranges are mapped to chunks at run time (`gold_targets` in `eval/run.py`, `eval/mapping.py`), so gold survives a change of chunker. |
| Sources | Third-party labels (CUAD contracts, QASPER papers, FinanceBench filings) plus authored fixtures for the strata no public set covers (versioned, injection, conflict, false premise, multi-turn). |

Gold status per split (the `audit_status` field of each item):

| Split | Status | What was done |
|---|---|---|
| Train, 118 items | Audited (`verified`) | A stronger independent model (`flagship`) reviewed all 123 items and queued 69 disagreements; a person decided each one: 9 items corrected, 5 dropped as ambiguous. |
| Dev, 59 items | Reviewed where disputed | The items that failed under the candidate configurations were checked against their sources by a person: 7 corrected (`verified`); the other 52 stay `draft`. |
| Test, 134 items | Draft, unaudited | Never reviewed, so that no person or model reads test content before the final run. |

The owner review covered 18 disputed items from train and dev under a fixed rule: gold changes only if the source does not support the claim, the claim is a verbatim-clause artifact, or it requires more than the question asks. It corrected 9 (2 train, 7 dev), rejected 2 proposals and left 7 unchanged. The record, with the change per item, is `review_2026_11_14` in `eval/datasets/vault_eval/audit_queue.yaml`; the rendered queue is `eval/datasets/vault_eval/audit_review.md`.

### 1.2 Graders and judges

- **Deterministic graders first** (`eval/graders.py`, applied per item by `eval/run.py`): citation integrity and in-scope checks, overlap with gold citations (chunk and sentence precision and recall), `must_not_contain`, prompt-injection violations, retrieval recall@8 and MRR@40. An item that a deterministic grader decides never reaches a judge.
- **Claim-level judges** (`eval/judge.py`, role `deep`): `eval_judge_claims@5` checks each required claim against the answer, `eval_judge_support@3` checks each answer segment against only its own cited passages, `eval_judge_abstain@1` checks that declining or correcting a false premise was the right behaviour. A "yes" that carries a quote the answer (or the cited passages) does not contain is overruled to "no": the model's quote is never trusted.
- **Bias controls**: reference-guided (the judge sees gold), blind (model names are scrubbed), judge tier at or above the generator tier, and a hash-selected subset re-judged by the `flagship` role for inter-judge agreement. Judge and generator come from the same vendor. This is mitigated, not eliminated.
- **Calibration status**: human labels for 200 (item, answer) pairs are in `eval/labels/labeled.yaml`, but the current judge versions are **not** calibrated against them (runbook step 7). The only measured agreement is the in-product `support_check` against the faithfulness judge: agreement 0.92, kappa 0.83, n = 37.

### 1.3 Statistics and run comparison

- **Paired comparison** (`eval/paired.py`): items are paired by id, with a 10,000-resample paired bootstrap CI. Recall uses an exact McNemar test. `--ledger` lists, item by item, what went from fail to pass and from pass to fail for a 0/1 metric such as `answer_pass`, with the stratum, so a net delta is never read without its gains and losses.
- **Run fingerprints** (`eval/fingerprint.py`): every sidecar (`eval/reports/<run_id>.json`) carries a `fingerprint` object, so two runs are compared only when they measured the same thing. Fields: `dataset` (`id`, `split`, `gold_sha`: a content hash of the loaded live items of the split, covering `golden.yaml`, `parts/*` and `authored/*`; `n_gold`), `items` (`n`, sorted `ids`, `sha`: the items actually scored), `filter` (the `--filter` spec), `judge` (`claims`, `support`, `abstain` as `id@version` from `PROMPTS` in `eval/judge.py`), `answer` and `code` (`git_sha`, `dirty` for tracked files). `answer` is what the server recorded, not local settings: `prompts` (union over the run's turns), `models` per role, `retrieval` and `switches` (every `chat_*` setting) come from `rag_runs.config`, and `index` (`chunker` from `document_versions.chunker_version`, `contextual_prefix`) from the ingested documents; a value that differed between turns reads `a|b`. When no turn was recorded, `answer` is `{"unavailable": true}` and the comparison refuses. The hashes are deterministic: canonical JSON, SHA-256, first 16 hex characters.
- **Strict paired comparison**: `python -m eval.paired A.json B.json --metric answer_pass` exits 2 and prints a field-by-field diff when the fingerprints differ. A field that may differ is named with `--allow <field>` (repeatable; a dotted path or its parent, for example `answer.prompts`, `retrieval`, `answer`, `judge`, `code`). Arms that evaluated different item sets need `--allow items`, which also covers `filter`. An unknown field name is refused. A sidecar without a fingerprint is refused.
- **Re-judging** (`eval/rejudge.py`): re-judges stored answers with the current judges, without re-answering. It writes `<run_id>.rejudged.json` beside its input (or in `--out-dir`) and refuses to replace an existing result unless `--overwrite` is passed, so an earlier re-judge is never lost by accident. `--write-sidecar` also writes `<run_id>.rejudged.sidecar.json` with the new metrics and the new judge versions in its fingerprint (its `code` and `items` are those of the re-judge), so a re-judged baseline compares against a fresh run with `--allow code` when the judged item sets match.
- **Clustered CIs and minimum detectable effect** (`eval/report.py`: `cluster_ci`, `design_effect`, `mde`): items from one document are not independent, so intervals cluster by source document. The MDE says when "no detectable difference" only reflects low power.
- **Run-to-run noise**: two runs of an identical configuration differ by about 2 points of `answer_pass` and 1 to 3 points of `support_rate`; the support judge alone moves about 1 point on a re-judge of the same answers. A delta inside that band is not read as a change.
- **Stage attribution** (`eval/attribution.py`): every failed item gets exactly one earliest faulty stage: extraction, chunking, retrieval, ranking, utilization, citation, generation, abstention or grading. An unknown signal is skipped, never guessed. Extraction and chunking are not yet observable and always read 0.

### 1.4 Gate tiers (`eval/gate.py`)

**Tier 0**, every change, $0. `make eval-gate-t0` (needs the compose Postgres) runs: dataset schema and coverage, the document map check, citation integrity of stored runs (default `eval/baselines/answer.json`), the retrieval-only comparison on the train split against the pinned retrieval baseline (`eval/baselines/retrieval.json`), and `pytest tests/integration/security`. The retrieval step scores the corpus of the eval user named by `EVAL_USER` when one exists, and otherwise uploads the train documents once.

**Tier 1**, prompt, model or routing changes, live on a sample. Produce a candidate sidecar with `make eval-answers SPLIT=dev MAX_USD=1`, then `make eval-gate CAND=eval/reports/<run_id>.json` (`GATE_ARGS=--strict-power` also fails on `INCONCLUSIVE`). `BASE` defaults to `eval/baselines/answer.json`, the final dev run; name another stored sidecar to compare against it. Exit 1 on any FAIL. The model call is never made by the gate itself.

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

### 1.6 Contamination check

A prompt that contains evaluation wording inflates the score of the items it echoes. `eval/contamination.py` compares every prompt that a default turn or a default judge run calls (analysis, answer, compare, support check, the reranker and the three judges) with every gold item's question, claims and accepted variants in every split. A match is a shared word 5-gram or a shared proper name, such as a party or document name; generic template phrasing is allowlisted. Matches are reported by prompt and item id only, and test-split text is never printed.

`cd backend && PYTHONPATH=.. uv run python -m eval.contamination` prints `8 prompts checked, 0 item matches` today and exits 1 on any match; `--all` checks every prompt version. It runs in `make ci` through `backend/tests/unit/eval/test_contamination.py`, so a prompt change that copies gold wording fails CI.

### 1.7 Three numbers, never mixed

| Number | Gold | What it answers |
|---|---|---|
| Locked benchmark | The original, pre-audit train gold, never edited | Did the system get better on a fixed yardstick? |
| Audited-gold score | The current gold | How good is the system against the best gold we have? |
| Engineering gain | Gold and judges held fixed, both runs scored the same way | How much did system changes move the score? |

A gold correction raises the baseline and the candidate alike; it is a benchmark correction and is never counted as engineering gain.

---

## 2. Results

The shipped configuration: `retrieval@5` (LLM listwise rerank of the top 50 fused candidates, top 8 kept, dot leaders collapsed in the reranker's copy), `analyze_query@4`, `answer_rag@12`, `answer_compare@3`, `support_check@1` (advisory). Judges `eval_judge_claims@5`, `eval_judge_support@3`, `eval_judge_abstain@1`. The frozen runs below were judged with `eval_judge_claims@4`, whose examples quoted two dev items' figures; re-judging the dev answers with the clean `@5` gives the same answer pass (0.847), and `python -m eval.contamination` now checks figures too. Gold for train and dev is the dataset at commit `81b8b26`. The baseline is the earlier default: fusion order without a reranker (`retrieval@2`) and an earlier answer prompt, its stored answers re-judged with the same judges and gold (train run `01a1da34`, dev run `01a1d9ef`).

Run reports are written to `eval/reports/<run_id>.md` with the metrics sidecar `<run_id>.json` and the answers `<run_id>.answers.jsonl`.

| Measure | Test (sealed) | Train | Dev |
|---|---|---|---|
| Run | `01a1db51-9481-7106-8c9c-64e1a87bacd6`, code `1c92d64` | `01a1db3b-ea38-7381-b619-8cbbd2700777` | `01a1db44-3d40-722a-8aac-3decbc9c66e6` |
| Items, gold | 134, draft (unaudited) | 118, audited | 59, reviewed where disputed |
| answer_pass, final | **0.761** (102), CI [0.674, 0.847] | **0.873** (103), CI [0.805, 0.932] | **0.847** (50), CI [0.750, 0.935] |
| answer_pass, baseline | not run | 0.763 (90) | 0.746 (44) |
| claim_completeness | 0.779 | 0.914 | 0.909 |
| support_rate (faithfulness), final / baseline | 0.957 / not run | 0.899 / 0.853 | 0.906 / 0.888 |
| abstention_correct | 0.913 (n = 23) | 1.000 (n = 25) | 0.909 (n = 11) |
| citation_integrity, citation_in_scope | 1.00, 1.00 | 1.00, 1.00 | 1.00, 1.00 |
| injection violations | 0 of 11 | 0 of 9 | 0 of 1 |
| must_not_contain passed | 14 of 14 | 18 of 18 | 4 of 4 |
| recall@8, mrr@40 | 0.753, 0.561 | 0.829, 0.612 | 0.816, 0.680 |
| cite_chunk_precision | 0.658 | 0.673 | 0.774 |
| Cost per turn, mean latency | $0.007, 7.3 s | $0.007, 7.4 s | $0.007, 7.7 s |
| Run spend, answers and judges | $3.41 | $1.41 | $0.70 |
| Regenerate | `make eval-answers SPLIT=test MAX_USD=4` | `make eval-answers SPLIT=train MAX_USD=3` | `make eval-answers SPLIT=dev MAX_USD=3` |

CIs are 95 percent, clustered by source document. The sealed test was run exactly once, on the frozen configuration, after every decision below was taken on train and dev; nothing was changed after it.

**Locked benchmark** (train, original pre-audit gold, 123 items): baseline 0.699 (86) and final 0.772 (95). It is the 118 final train answers re-judged against the gold as it was before the audit (commit `f352008` is the audit; its parent holds the original gold), plus the 5 audit-dropped items answered under the final configuration (run `01a1db48`, `EVAL_ARGS=--only-dropped`) and judged against their original gold. Regenerate in a scratch checkout of the repository: put the old gold in place with `git archive f352008~1 eval/datasets/vault_eval_v2 | tar -x && rm -r eval/datasets/vault_eval && mv eval/datasets/vault_eval_v2 eval/datasets/vault_eval`, then `cd backend && PYTHONPATH=.. uv run python -m eval.rejudge --answers <run_id>.answers.jsonl --out-dir <dir> --max-usd 1` for each run.

**Engineering gain** (gold and judges fixed): +11.0 points on train (90 to 103 of 118) and +10.2 on dev (44 to 50 of 59). Regenerate: re-judge the baseline answers with `--write-sidecar --out-dir <dir>`, then `cd backend && PYTHONPATH=.. uv run python -m eval.paired <baseline>.rejudged.sidecar.json ../eval/reports/<final_run_id>.json --metric answer_pass --allow answer --allow code --ledger`.

Against the quality targets fixed before measuring (answer_pass 0.80, faithfulness 0.90, cite_chunk_precision 0.85), the test run meets faithfulness and misses answer_pass and chunk precision. They are recorded, not relaxed.

Other measurements:

| Measure | Value | Regenerate |
|---|---|---|
| Retrieval-only, `retrieval@2` (fusion order, no reranker), train, 105 of 118 items with mapped gold: recall@8, mrr@40 | 0.838 (clustered CI [0.767, 0.901]), 0.621 (CI [0.526, 0.707]); the frozen `eval/baselines/retrieval.json` | `make eval-retrieval` (`EVAL_USER=<an eval user whose corpus holds the train documents>` reuses it; $0 beyond cached query embeddings) |
| Lexical leg alone (OR query), same run setup: recall@8, mrr@40 | 0.675, 0.463 | `EVAL_RANK=lexical make eval-retrieval SPLIT=train RETR_CONFIG=retrieval@2` |
| Fused ranking before the rerank, same run setup: recall@8, mrr@40 | 0.838, 0.623 | `EVAL_RANK=rrf make eval-retrieval SPLIT=train RETR_CONFIG=retrieval@2` |
| Whole-document versus retrieval answers, 39 paired single-document train items: answer_pass | 0.692 (retrieval) versus 0.641 (whole document), no detectable difference; cost per turn $0.0063 versus $0.0120 | `make eval-answers SPLIT=train MAX_USD=1.5 FILTER=scope_docs=1` with `FULL_CONTEXT_AUTO=true`, at commit `1c92d64`, where that switch exists |
| `support_check` against the faithfulness judge | agreement 0.92, kappa 0.83, n = 37, $0.14 | at commit `0c5d8d1`: `cd backend && PYTHONPATH=.. uv run python -m eval.support_calibration --n 40 --max-usd 0.5` |
| Citation integrity (no unresolved citation reaches the user) | 1.000 on every kept run | `make eval-gate-t0` |
| Gold quotes locatable in the extracted PDFs (the earlier 100-quote expert set) | 100.0 percent | at commit `0c5d8d1`, after `make eval-fetch`: `make eval-extraction` |
| Brief facts verified by code, 3 sample documents | 56 of 58 | `python3 -c "import json;d=json.load(open('eval/baselines/brief_samples.json'));f=[x for v in d.values() for x in v['facts']];print(sum(x['status']=='verified' for x in f),len(f))"` |

---

## 3. Where the test run fails

The test split is not diagnosed item by item: reading its failures to change the system would be tuning on test. The per-stratum table of the sealed run (`eval/reports/01a1db51-9481-7106-8c9c-64e1a87bacd6.md`, written by the test command in section 2) shows where the 32 failures sit:

| Stratum | Items | answer_pass | recall@8 |
|---|---:|---:|---:|
| conflict | 5 | 0.200 | 0.433 |
| aggregation | 5 | 0.400 | 1.000 |
| compare_docs | 13 | 0.615 | 0.734 |
| multi_hop | 11 | 0.636 | 0.909 |
| multi_span | 11 | 0.636 | 0.879 |
| numeric_calc | 10 | 0.700 | 0.300 |
| single_span, conditional, false_premise, multi_turn | 46 | 0.75 to 0.89 | 0.78 to 1.00 |
| injection, unanswerable, table_lookup, versioned | 33 | 1.000 | |

Multi-document questions (conflict, aggregation, compare) are the weakest strata. `table_lookup` and `numeric_calc` pass at 1.000 and 0.700 with recall@8 of 0.143 and 0.300; the cause of that gap is not investigated, because that would mean reading test items. The strata are small (5 to 20 items), so a single item moves a stratum by 5 to 20 points.

On train and dev, where failures are read (by hand, from the stored runs and `make eval-diagnose`): before the reranker, the largest group of real train failures (12 of 27) lost the gold chunk between fusion and the top 8, which led to `retrieval@5`. Under `retrieval@5`, 7 of 15 dev failures were disputed gold or judge items and 3 of the other 8 were retrieval-side; the disputed gold went to the owner review in section 1.1.

---

## 4. Decisions made from measurements

| Decision | Evidence |
|---|---|
| The lexical leg ORs the question's terms. | The lexical leg alone reaches recall@8 0.675 and mrr@40 0.463 on train (section 2), against 0.838 and 0.621 for the fused ranking. |
| **LLM listwise reranker over 50 candidates** (`retrieval@5`, the default). | Paired answer runs against `retrieval@2` (no reranker), same answer prompt: dev answer_pass 0.661 to 0.746 (+8.5, clustered CI [0.0, +17.9], 6 gained, 1 lost), train 0.737 to 0.864 (+12.7, CI [+6.5, +19.8], 17 gained, 2 lost); support_rate -1.4 (dev) and -2.8 (train), inside the run-to-run spread; injection 0 and citation integrity 1.00 in every run; +$0.0004 per turn. With 20 candidates the dev gain was +1.7 (not detectable): gold chunks sat at fused ranks 11 to 44. Collapsing dot leaders in the reranker's copy lets financial-statement rows fit the window. |
| **Answer prompt with detail discipline** (`answer_rag@12`, the default). | The rules: answer every part asked; keep qualifiers and every cited detail; no label, superlative, unit, count or figure the cited sentences lack; the latest version only for current-terms questions; a document that lacks the asked statement is named together with what it does say. Paired against the previous default answer prompt on `retrieval@5`: support_rate +4.5 on train (CI [-0.6, +9.4]) and +2.7 on dev (CI [-3.8, +10.6]), answer_pass no detectable difference, guards unchanged. `answer_rag@12` is that prompt with neutral examples (section 1.6). |
| **Decontaminated default prompts and judges** (`analyze_query@4`, `answer_rag@12`, `answer_compare@3`, `eval_judge_claims@5`, `eval_judge_support@3`). | The contamination check found evaluation wording or document names in the earlier defaults: test-split document names in the analysis prompt, dev and train gold wording in the answer, compare and judge examples. The new versions change only the examples. Every final number in section 2 was measured with them, and the baseline was re-judged with the same judges. |
| **Evidence gate off** (`gate.enabled: false`). | With the calibrated floor, 3 of 44 answerable train items were refused. The floor value stays in `backend/config/retrieval@2.yaml` for when scored abstention replaces it. |
| **Whole-document route only on request.** | No gain (0.692 versus 0.641, no detectable difference) at about twice the cost per turn. |
| **Support check on, advisory** (`support_check@1`). | Agreement 0.92 and kappa 0.83 against the faithfulness judge. It annotates verdicts and never blocks an answer. |
| Measured and not adopted, so deleted from the code: plan-first answers, a support check that repairs the answer, calculation segments, number-aware lexical variants, a compare quota with a repair call, a voice fragment, a shorter analysis prompt. | Dev split, paired, earlier judges: plan-first +1.7 answer_pass with a faithfulness CI lower bound of -3.0 (fails the -2 margin); support repair -4.4 faithfulness and slower; calculation segments 0.0 on 10 numeric items (too few to decide; built on the plan-first prompt and never measured apart from it); number-aware lexical recall@8 showed no detectable difference; the others inside noise or underpowered. The switches exist at commit `1c92d64` for anyone who wants to re-run them. |
| Citations are application-managed (handles, code-copied text). | A spike over 12 items measured 0 invalid handles and 15 of 15 segments cited; citation_integrity is 1.000 on every kept run. |
| PDF extraction uses `use_text_flow=True, x_tolerance_ratio=0.15`. | On the earlier 100-quote expert set, 99 of 100 gold quotes located, against 86 of 100 with the library defaults; two-column papers 22 of 23 against 9 of 23 (re-runnable at commit `0c5d8d1`). |

---

## 5. Runbook

An ordered cycle for the golden set. Run from the repository root, one heavy command at a time (check `uptime`; wait if the load average is above about 8). Every live step has a hard `MAX_USD` cap. A run that reaches the cap exits 3 with a partial result and freezes nothing. Keys live only in the gitignored `.env`. "Decide" marks a point where a person reviews before the next step.

**0. Prerequisites ($0).**

```bash
make eval-docs ARGS=--download        # fetch the source documents
make eval-docs ARGS=--check           # verify the document map
cd backend && PYTHONPATH=.. uv run python -m eval.contamination   # no default prompt shares wording with the gold
```

Set `BRIEF_ENABLED=false` in `.env` so evaluation uploads do not pay for briefs. The worker reads `.env` through `env_file`; a shell export does not reach it. Start or recreate the stack (`make up`), then confirm with `docker compose exec -T worker printenv BRIEF_ENABLED`, which must print `false`. The default reranker is an LLM call and needs no extra container.

**1. Train run (answers and judges).**

```bash
make eval-answers SPLIT=train MAX_USD=3
```

Writes `eval/reports/<run_id>.md`, `<run_id>.json` (metrics sidecar) and `<run_id>.answers.jsonl` (answer text, cited passages, flags; the input to steps 2 and 7). Do not freeze on train: it exists to find system and gold problems. Decide: read the per-stratum table and any degraded or errored items.

**2. Independent gold audit** (cap $2, role `flagship`).

```bash
make eval-audit ANSWERS=eval/reports/<run_id>.answers.jsonl MAX_USD=2
```

Writes `eval/datasets/vault_eval/audit_queue.yaml`. Re-running refuses to overwrite a queue that already holds human decisions. A free rehearsal: `cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --answers ../$ANSWERS --dry-run --limit 5`.

**3. Human review ($0, human time).** Render the queue with `cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --render ../eval/datasets/vault_eval/audit_queue.yaml --render-out ../eval/reports/audit_review.md`. Fill `decision` for every queued item in `audit_queue.yaml`: `gold_wrong`, `gold_incomplete` (add the full corrected list as `fix`), `ambiguous` (the item is dropped; add a `note`) or `system_wrong`. A gold fix that only deletes claims the system fails would bias the set towards the system, so each proposal is read against its source, not bulk-applied.

**4. Apply the decisions ($0).**

```bash
make eval-audit-apply APPLY_ARGS=--check     # validate, print the summary, write nothing
make eval-audit-apply                        # surgical edits of the dataset part files
make eval-docs ARGS=--check
```

It refuses (exit 2, nothing written) on a missing decision or a stale queue. Review `git diff eval/datasets/vault_eval` and commit before any further run, so later runs see the audited gold. Re-judge the stored baseline answers against the new gold (`python -m eval.rejudge ... --write-sidecar --out-dir <dir>`) so that baseline and candidate are scored alike.

**5. Dev run (feedback for tuning and for accepting switches).**

```bash
make eval-answers SPLIT=dev MAX_USD=3
```

No freeze. Compare a candidate with the baseline using `python -m eval.paired ... --ledger` and `make eval-gate` (section 1.4).

**6. Sealed test run (once).**

```bash
make eval-answers SPLIT=test MAX_USD=4
```

Run it once, on a configuration frozen beforehand, and report the result as it comes. The final runs behind every number above (train `01a1db3b`, dev `01a1db44`, the five recovered train items `01a1db48`, test `01a1db51`) are kept in `eval/baselines/final/` (report, table and answers per run), and `eval/baselines/answer.json` is the dev run, the default `BASE` of `make eval-gate`. `EVAL_ARGS="--freeze answer"` also writes `eval/baselines/answer.json` (per-stratum table, clustered CIs, MDE inputs and attribution) for later gates. A cheaper injection-only check: `make eval-answers SPLIT=test FILTER=stratum=injection MAX_USD=0.5`.

**7. Label export for judge calibration ($0).**

```bash
make eval-labels ANSWERS=eval/reports/<run_id>.answers.jsonl N=200
```

Writes `eval/labels/labeled.yaml` (about 200 pairs, about 40 percent failures, null labels, split by source document). After the human labels (see `eval/labels/README.md`), calibrate with `cd backend && PYTHONPATH=.. uv run python -m eval.judge --calibrate ../eval/labels/labeled.yaml --max-usd 3`.

Retrieval-only measurements cost cents and need no judge: `make eval-retrieval SPLIT=train RETR_CONFIG=<config>`. Where a run's gold passages were lost, offline: `make eval-diagnose RUN=<run_id>`. The answers-file shape is checked by `cd backend && uv run pytest tests/unit/eval/test_answers_dump.py -q`.

Budget caps per step: train $3, audit $2, dev $3, test $4 (the sealed run spent $3.41), calibration $3. Record the printed `spent_usd` of each step.

---

## 6. Open measurement work

Nothing below is measured today. Each item names what closes it.

- **Test gold audit.** The test gold is an unaudited draft; an audit after the sealed run (runbook steps 2 to 4 on the test answers) would turn the 0.761 into an audited-gold score. It must not feed back into tuning.
- **Dev gold audit** of the 52 dev items that were not disputed and so never reviewed.
- **Judge calibration** of the current judge versions against the 200 human labels (runbook step 7), with kappa and true-negative-rate checks on the claim, support and abstention judges.
- **Paired dev-split evaluations of the switches that are off** (see Behaviour switches in `ARCHITECTURE.md`): scored abstention, the locate loop, the second chunker and contextual index text. The retrieval-only arms (chunker, index headers) cost cents after a re-ingest.
- **Stage attribution for extraction and chunking**, which reads 0 for lack of a signal.
- **The `formatting` stratum** has 0 of 5 items (multi-column, scanned page, tracked changes).
- **`table_lookup` gold** is model-drafted.
- **Live time to first token** (`make eval-answers SPLIT=dev MAX_USD=0.3 FILTER=stratum=single_span EVAL_ARGS=--stream`); the last measurement is 3.3 s against a 2.0 s target, taken before the current configuration.
- **Per-document embedding cost, documents per hour per worker, exact search at 100,000 chunks and any concurrency figure** (see `docs/operations.md`).
