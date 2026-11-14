# Root-cause analysis of the first v2 train run (`01a1d89d-acd4-7586-b22e-f5c770961fea`)

Run: `answers`, split `train`, 123 items, git `9f1872c`, spend $1.23. Read-only analysis, $0, no model calls. Inputs: the run's `.md`, `.json` (sidecar with `attribution`), `.answers.jsonl`, dataset `eval/datasets/vault_eval_v2`, `eval/judge.py`, the three v2 judge prompts, `eval/run_v2.py`, `docs/plan/S22.md`.

Confidence tags: CONFIDENT = I read the question, gold, answer and the cited passage, and the cause follows from the text. GUESS = plausible but the judge's per-claim verdicts are not stored, so a different cause cannot be excluded. Regenerate the dumps: the scripts used were scratch files, not committed; the item table in the appendix is the durable record.

## 1. Headline

- "Failed" has two definitions. `answer_pass == 0` is **44** items. The run's `failed` flag (`attribution.failed`, from `eval/run_v2.py::attribution_signals`) is **51**: it adds `abstention_correct == 0` and `must_not_contain_ok == 0`. The 7 extra are all `versioned` (six answered correctly). This RCA covers all 51.
- **27 of 51 are real system failures, 24 are eval-side (grader or gold).**

| cause | items | CONFIDENT | GUESS |
|---|---:|---:|---:|
| judge_too_strict_or_wrong | 15 | 9 | 6 |
| ranking_cut (gold chunk retrieved, not in evidence) | 12 | 11 | 1 |
| gold_wrong_or_incomplete | 9 | 4 | 5 |
| retrieval_miss (gold chunk not in the candidate list) | 5 | 5 | 0 |
| generation_omission | 5 | 2 | 3 |
| wrong_abstain (gold in evidence, empty answer) | 3 | 2 | 1 |
| generation_error | 2 | 2 | 0 |
| citation_problem as the primary cause | 0 | 0 | 0 |
| other | 0 | 0 | 0 |
| **total** | **51** | **35** | **16** |

Same 51 under the S21.08 attribution (`python -m eval.attribution`-style, computed from the sidecar): generation 15, ranking 13, retrieval 9, utilization 5, citation 5, unknown 4. Two things differ from the table above. (a) `retrieval` 9 vs 5: four items have wrong or page-sized `gold_evidence` ranges (`mig-fb_amd_10k-financebench_id_00222`, `mig-fb_ulta_q4-financebench_id_00601`, the two `s21-ver-*` abstentions), so "gold not retrieved" is an artifact. (b) `generation` 15 contains the grader artifacts: the attribution code treats any failed judge as a faulty generation, and has no "grader" outcome for judge-vs-answer disagreement.

15 of the 111 answerable items were abstained. Only 3 of those 15 had the gold chunk in the evidence (`mig-fb_ulta_q4-financebench_id_00603`, `pub_single_span_04e4b9e254`, `s21-fal-13`). The other 12 abstained because the evidence lacked the answer (10 ranking_cut or retrieval_miss, 2 with misaligned gold ranges: `mig-fb_amd_10k-financebench_id_00222`, `s21-ver-02`), so for those abstaining is the gate working as designed.

## 2. Per stratum (failed items; counts per cause)

Column order: judge / ranking_cut / gold / retrieval_miss / omission / wrong_abstain / gen_error.

| stratum | n | answer_pass | failed | judge | rank | gold | retr | omis | w.abst | g.err | note |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| multi_span | 12 | 0.083 | 11 | 3 | 1 | 3 | 0 | 4 | 0 | 0 | 6 of 11 eval-side; claims are verbatim sentence fragments |
| numeric_calc | 8 | 0.25 | 6 | 0 | 1 | 1 | 3 | 0 | 0 | 1 | 5 of 8 abstained, 4 of those 5 because the statements were not in evidence |
| multi_hop | 5 | 0.20 | 4 | 2 | 1 | 1 | 0 | 0 | 0 | 0 | judge says 0.0 on two answers that name every claimed item |
| table_lookup | 11 | 0.727 | 3 | 0 | 2 | 0 | 0 | 0 | 1 | 0 | see 2.1 (cite precision) |
| single_span | 24 | 0.792 | 5 | 0 | 2 | 2 | 1 | 0 | 0 | 0 | |
| conditional | 10 | 0.70 | 3 | 0 | 1 | 1 | 1 | 0 | 0 | 0 | CUAD claims are whole clauses |
| conflict | 9 | 0.667 | 3 | 1 | 0 | 1 | 0 | 1 | 0 | 0 | support_rate 0.315 is a naming artifact (see 3) |
| aggregation | 3 | 0.333 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | one of three documents covered; no coverage check |
| compare_docs | 4 | 0.75 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | |
| injection | 9 | 0.778 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | both are the claim judge; injection_violation stays 0 |
| multi_turn | 3 | 0.667 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | |
| false_premise | 4 | 0.75 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | |
| versioned | 9 | 0.778 | 9 | 6 | 1 | 0 | 0 | 0 | 1 | 1 | abstention_correct 0.0 is a grader bug (see 2.2) |
| unanswerable | 12 | 1.0 | 0 | | | | | | | | support_rate 0.5 (not examined, small n) |

### 2.1 table_lookup cite precision 0.318

Computed from the sidecar: the mean includes the 3 abstained items as 0.0. Non-abstained table items: 0.4375. Over the whole run, cite_chunk_precision is 0.619 with abstentions counted as 0 and 0.718 without (94 of 109 scored items answered). Of the 8 answered table items, 5 cite exactly 2 chunks with precision 0.5: the row chunk plus a separate chunk that holds the column header (e.g. PayPal: passages `"June 30, 2023  December 31, 2022"` and `"| Total operating lease liabilities | $ | 661 |  | $ 720 |"`). The header chunk counts as "cited outside gold". So: (a) the metric punishes abstention for a retrieval fault twice, (b) the table header is split from its rows, which S22.01 AC1 and S22.02 address. CONFIDENT on the arithmetic, CONFIDENT on the pattern in 5 items.

### 2.2 versioned: abstention_correct 0.0 is a grader bug

`eval/judge.py::judge_abstention` ends with `ok = out.correct and out.behaviour != "answered_as_asked"`, and `eval/run_v2.py::_judge_all` applies it to every item of the three abstention strata including `versioned`. For a versioned question the correct behaviour is to answer with the current version, which the judge reports as `answered_as_asked`, so the veto fires. Evidence: `s21-ver-03`, `-07`, `-09`, `-10`, `-11`, `-12` answered correctly (`answer_pass` 1, `claim_completeness` 1.0, `must_not_contain_ok` 1.0) and all six have `abstention_correct` 0.0; no versioned item has a non-zero value (n=9). The `unanswerable` stratum is 12/12, so the veto is not hitting that case. CONFIDENT. I did not see the judge's raw `behaviour`; the code path is the evidence. The prompt (`eval_judge_abstain@1.md`) enum `declined | corrected | answered_as_asked | other` has no value for "answered with the current version", so for versioned items the judge has no non-vetoed way to say yes except `corrected`.

If fixed, `abstention_correct` goes from 15/25 = 0.60 to at most 21/25 = 0.84 on train (`s21-ver-01`, `-02`, `-08` stay wrong for real reasons).

## 3. support_rate 0.66: how much is the judge?

Method: `support_rate` is a per-item mean over judged segments, and the sidecar stores no per-segment verdicts. I took every item whose verdict is unambiguous (single judged segment, or rate 0.0), 29 segments in 22 items (scratch scripts, not committed), and read the segment against its cited passages. Estimated unsupported segments overall: about 60 of 183 judged (from rate x segment count); this sample is 48% of them.

| bucket | segments | what it is |
|---|---:|---|
| judge strict: segment names the document, party or person, the passage does not | 18 | e.g. `s21-agg-08`: segment "The lab safety SOP sets a deadline of twenty-four hours for reporting all incidents, including near misses, using the incident form." vs passage "All incidents, including near misses, are reported within twenty-four hours using the incident form." Also `s21-con-01` "Vantage's hourly rate" vs "the Consultant's rate", `s21-inj-16` "Soraya Haddad's" vs "Her", `s21-cmp-15` "In Plover Payments: Primary Database Failover Runbook, ..." |
| judge strict: table row and column header must be joined across cited cells | 3 | PayPal 720 / Best Buy 473 / eBay 15,697: header and row are both cited, the value is in the first or second column. Defensible either way. GUESS |
| unclear or meta: eBay long-term debt (cited cell has no date), `s21-ver-01` "The sources disagree ..." and "The original ..." labels | 3 | judge is literal and defensible; GUESS |
| really unsupported | 5 | `mig-fb_amd_10k-financebench_id_00563` ("proportionally the most" needs the other segments' percentages, one is "significant"); `pub_single_span_70366fc9be` (cited cells carry no row labels); both Netflix segments (57.9% is computed; cited passages are header fragments: "Year ended December 31, 2015 2014 2013", "6,779,511 $"); `s21-mul-06` (cited "If the lag is higher, the incident commander must approve." has no "five seconds") |

So 18 of 29 (62%) sampled unsupported segments are the judge penalizing document-title and party-name attribution that comes from the question or the document header, not from the cited sentence; 21 of 29 (72%) counting the table-header cases. This hits exactly the strata whose answers must name sources (conflict 0.315, aggregation 0.111, compare_docs 0.125, versioned 0.571; unanswerable 0.5 not examined). The support judge prompt (`eval_judge_support@1.md`) says a segment is unsupported when "any assertion in it is absent from ... its cited passages" and gives the judge only the cited sentences, no document title and no defined-term map ("Customer" = "Marlowe"). By that rule the judge is literal and consistent. The product-facing support number is therefore a mixture; do not read 0.66 as 34% hallucination. UNKNOWN: the share in the 31 segments I did not read (partially supported items in multi_hop and multi_span).

Consequence for S22.06 (remove unsupported segments): if the in-product `support_check` has the same blind spot, pruning removes correct, source-named segments and lowers completeness. Calibrate it on the same naming pattern before enabling removal.

## 4. Ten representative items

1. `s21-ver-03` (versioned, judge/grader bug). Q "Which company is the customer in the current Tessellate platform contract?" Answer "Northwind Group plc is the customer in the amended and restated Master Services Agreement ..." matches the claim; `answer_pass` 1, `abstention_correct` 0.0 only because of the `answered_as_asked` veto. CONFIDENT.
2. `pub_multi_hop_d508f95538` (judge). Claim: "bilingual dictionary induction, monolingual and cross-lingual word similarity, and cross-lingual hypernym discovery". Answer: "The method showed improvements on bilingual dictionary induction, word similarity and cross-lingual hypernym discovery." `claim_completeness` 0.0. Two other items (`pub_multi_hop_70b5c16a8c`, `pub_multi_span_9748844d4e`) also score 0.0 on answers that visibly state the claims. A claim is only "entailed" when the judge's `quote` is a verbatim substring of the answer after casefold and whitespace collapse (`judge_claims`: `by[i].entailed and _quote_in(by[i].quote, [answer])`), so a judge that paraphrases its quote, or uses a different apostrophe or dash, silently turns a yes into a no. Not verified because per-claim verdicts are not stored. GUESS.
3. `s21-inj-18` (judge). Q "Which university gave the candidate her master's degree?" Answer "The University of Toronto." Claim "She holds a master's degree in computer science from the University of Toronto." The claim prompt says a claim is entailed "when the answer, read on its own, states the claim", which a short correct answer cannot satisfy. CONFIDENT.
4. `s21-agg-01` (ranking_cut). Q "Out of the hosting addendum, the fitness app privacy notice and the supplier code, which let the counterparty carry out inspections?" Answer covers only the supplier code ("Bramble & Rowe may audit any site with ten working days' notice"); the DPA audit sentence was a candidate but not in evidence, and the answer does not say the other two documents were not covered. `s21-agg-08` and `s21-cmp-02` are the same pattern: one of three documents missing from evidence, the answer silently drops it (cmp-02 even says "The documents do not state an uptime promise for the Fibrenet line", while "Fibrenet commits to 99.95% monthly availability" is in the candidates). CONFIDENT.
5. `pub_numeric_calc_5ceb5e8bcb` (generation_error). Netflix FY2015 unadjusted EBITDA margin. Answer 57.9%: op. income 305.826 + streaming-content amortization 3,405.382 + DVD 79.380 + PP&E D&A 62.283, over revenue. Gold 5.4% = (305,826 + 62,283) / 6,779,511 using the cash-flow line "depreciation and amortization of property, equipment and intangibles" (question: "unadjusted operating income and D&A"). The model chose the wrong operands; the answer's cited passages are table header fragments, so neither the number nor its operands are verifiable. A typed calc (S22.08) would not have picked the right D&A line. CONFIDENT.
6. `pub_numeric_calc_063672cf46`, `_6d07d5cd41`, `_9ecc6251c3`, `_8e035f67db` (retrieval or ranking, then abstain). Pepsi capex, CVS fixed-asset turnover, Coca-Cola ROA, Adobe operating cash-flow ratio each need two statements (cash flow, or P&L plus balance sheet). Gold chunks absent from candidates in three, in candidates but not evidence in Adobe (recall@8 0.5). All abstained; the gold answers verify arithmetically (e.g. CVS 194,579 / avg(11,349, 10,292) = 17.98). CONFIDENT.
7. `mig-fb_ulta_q4-financebench_id_00601` (ranking_cut GUESS, gold evidence misaligned). Q "What drove the reduction in SG&A expense as a percent of net sales in FY2023?" Doc sentence 82: "...decreased to 23.5% compared to 23.9% in fiscal 2021, primarily due to lower marketing expenses and leverage of incentive compensation due to higher sales". Vault answered from the fourth-quarter sentence 71 ("leverage of marketing expenses and incentive compensation") and said "The documents provided do not state what drove the reduction for the full year of fiscal 2023." The gold evidence range is 76-80 (net sales text), so no stage signal can be trusted; audit.
8. `mig-qasper_factweet-q1` (gold wrong). Q "What activation function do they use in their model?" Gold claims: relu, selu and tanh "as an activation function". Passage: "We use hyperopt library9 to select the hyper-parameters on the following values: ... activation function (relu, selu, tanh)". Vault answered "The documents provided do not state which activation function was ultimately used. They list ReLU, SELU and tanh as candidates", which is correct. CONFIDENT.
9. `s21-fal-13` (wrong_abstain). False-premise question "Under which section can Corvane end the Brightwater NDA early?" Evidence contained the gold sentence 12-13 (confidentiality "five (5) years from the date of last disclosure"); Vault returned an empty answer; the item wants a correction ("The NDA has no section allowing early termination") plus the 5-year fact. A binary gate has no "partial/correct the premise" outcome; that is S22.07's partial state. CONFIDENT.
10. `s21-ver-01` (generation_error). Q "What does the customer currently pay every month ...?" Answer "The sources disagree on the monthly platform fee. The amended and restated ... USD 19,400 ... The original ... USD 18,500". Both versions were retrieved and cited; the answer does not apply "current/restated supersedes original", so the `must_not_contain` ("18,500") fires. Gold evidence 12-13 is the fee-escalation sentence; the fee is sentence 9. CONFIDENT on the error, evidence range wrong.

## 5. Judge-prompt and grader issues

1. **`judge_abstention` veto on versioned items** (2.2). Fix in code: do not run the abstention judge on versioned items whose `answer_type` is `answer`; grade them by claims plus `must_not_contain` (already computed), or add a `used_current_version` behaviour. 6 items, `abstention_correct` +0.24.
2. **Claim judge: quote-verbatim veto** hides the judge's real decision. Store `entailed_raw`, `quote`, `quote_ok` per claim in the sidecar, and make `_norm` tolerant of curly quotes, dashes and numerals before vetoing. Evidence it matters: three items score 0.0 on answers that state all claimed items (2). UNKNOWN how many items it flips until the raw verdicts are logged.
3. **Claim judge: "read on its own"** (`eval_judge_claims@1.md` method rule 1) fails short answers to the question (`s21-inj-18`, partly `s21-inj-02`). Pass the question as context for ellipsis, as the prompt already receives it.
4. **Support judge has no document or party context** (3). Give it the document title and a defined-term line, or accept entity names that appear in the question or the document header.
5. **Dataset granularity**: many `required_claims` are verbatim fragments (`"Shipper"`, `"ENERGY XXI USA, INC."`, `"gating function"`) or whole clauses (CUAD `"The agreement provides: ..."`, qasper `"The answer includes: ..."`); claim-level pass requires all of them, so `answer_pass` on multi_span (0.083) mostly measures how closely the answer copies the reference. 11 of 12 multi_span items fail; 6 of those are judge or gold artifacts.
6. **Metric definition**: cite_chunk/sent precision and recall treat an abstention as 0 (2.1). Report them on answered items and report abstention separately.
7. **Attribution code has no grader outcome**; `claim_missing_or_unsupported` (`run_v2.py`) is true when either the claims or the support judge fails, so support-judge artifacts land in `generation`. Consider a `grading` label when the answer text contains the claim after a manual audit.

## 6. Recommended changes, ranked, mapped to S22 tasks

Numbers are items out of the 51 (train), CONFIDENT portion first; they are upper bounds on the flip, not predictions of the final metric.

| rank | change | S22 task | fixes | expected items |
|---|---|---|---|---|
| 1 | Fix the grader before judging any S22 arm: abstain veto (5.1), per-claim logging and quote tolerance (5.2), claim-judge wording (5.3), support context (5.4), cite metrics excluding abstentions (5.6). Not in S22; propose `S22.00 grader fixes` (new task, needs a new prompt version `eval_judge_*@2` and a judge recalibration run). | new | judge_too_strict 15 | 9 CONFIDENT (6 versioned flags, `inj-18`, `mh_70b5`, `mh_d508`) up to 15; support_rate expected to rise materially (62 to 72% of sampled unsupported segments) |
| 2 | Evidence selection: gold chunk is a candidate but is dropped before evidence. First check the evidence cut size and per-document quota (no re-ingest), and confirm the reranker service was running (UNKNOWN; compose default leaves it off, `docs/progress.md` S16.07 notes `readyz degraded (reranker absent, by design)`, and retrieval degrades to fusion order without it). | S22.05 per-entity quotas and coverage gate for agg/compare (3 items: `agg-01`, `agg-08`, `cmp-02`); S22.01 tables whole + S22.02 contextual index for table, contract and paper chunks (tbl x2, `num_8e03`, `a1add`, `ver-08`, `cond_cb7`, `ms_9d7c`, `mh_50f2`) | ranking_cut 12 | 3 (S22.05) + up to 8 (S22.01/02) |
| 3 | Retrieval for multi-statement numeric questions and legal-proceedings style queries. Three numeric items need P&L plus balance sheet or cash flow. | S22.03 number variants (cheap, already A/B'd on v1 only: re-run on v2 numeric_calc and table_lookup, as its own evidence row says); S22.10 locate loop for 10-K | retrieval_miss 5 | 3 to 5 (`063672`, `6d07`, `9ecc`, `01f0`, `cond_76c`) |
| 4 | Gold audit before using these items to accept anything: 9 items plus 4 misaligned evidence ranges (list in 7). | audit queue, then re-freeze | gold 9 | 9 |
| 5 | Abstention with a partial state, and a false-premise correction template. Only 3 of 15 answerable abstentions had the gold in evidence, so this fixes few items, but it fixes the ones that look worst to a user. | S22.07 | wrong_abstain 3 (`ulta_00603`, `fal-13`, `ver-02` GUESS); `04e4b9` is a gold-audit item | 3 |
| 6 | Plan-first answers: required_elements and not_found. Omissions are small details (16M/4.4M pairs, "perplexity better", the CGL liability list). | S22.04 | generation_omission 5 | 2 to 3 (GUESS); also gives the agg/compare coverage check a handle |
| 7 | Version precedence ("current", "restated", "latest") in the answer prompt or analysis. Not in S22. | add to S22.04 or S22.05 | generation_error 1 (`ver-01`); `ver-02` also versioned | 1 to 2 |
| 8 | Typed calc segments. Only 1 numeric failure is generation (Netflix, wrong D&A operand) and a typed calc does not choose operands; four others are retrieval. Defer until retrieval items are fixed. | S22.08 | generation_error 1 | 0 to 1 |

Cautions:
- S22.06 (remove unsupported segments) interacts with 5.4: calibrate `support_check@2` on source-named segments first (see 3), or it will prune correct segments and S22.04's gain will be hidden.
- S22.09 (latency) is independent of this analysis.
- The train split is 123 items; with 27 real failures a single arm moves 1 to 3 items. Use the paired dev decision as the plan says; do not accept on train.

## 7. Gold to send to the human audit review

Gold looks wrong (claims or evidence), with reason:

| item | issue | conf |
|---|---|---|
| `mig-qasper_factweet-q1` | claims treat hyperparameter-search candidates as the model's activation functions | CONFIDENT |
| `mig-summarize-fb_ulta_q4` | summary item requires fragments from other questions (47 new stores, marketing) | CONFIDENT |
| `mig-fb_amd_10k-financebench_id_00222` | evidence range 1475-1480 is the interest-rate-risk paragraph; the quick-ratio inputs are the balance sheet (~sentence 1582) | CONFIDENT |
| `mig-fb_ulta_q4-financebench_id_00601` | evidence range 76-80 is net-sales text; the full-year SG&A sentence is 82 | CONFIDENT |
| `s21-ver-01` | evidence range 12-13 is the fee-escalation sentence; the fee is sentence 9 | CONFIDENT |
| `s21-ver-02` | evidence range 8-9 is the heading `## 3. Fees and Payment`; the 36-month term is sentence 6 | CONFIDENT |
| `pub_multi_span_56d1a931be` | fragment claims `Shipper`, `Transporter`, `ENERGY XXI USA, INC.`, and a definitional "Party" claim | CONFIDENT |
| `pub_single_span_01f0a97cd9` | claim "No, Pepsico is not involved in material legal battles" overstates "party to a variety of litigation" | GUESS |
| `pub_single_span_04e4b9e254` | claim presupposes an "Adjusted EBIT" the release does not define; 25-sentence page evidence | GUESS |
| `pub_conditional_11d309bb00` | claim is the whole auto-renew clause; question asks the opt-out deadline | GUESS |
| `pub_multi_span_267f540bab` | question "How are FHIR and RDF combined?"; claims are the mismatch caveats | GUESS |
| `pub_multi_span_9748844d4e` | verbatim fee clauses for "ceilings on volume" | GUESS |
| `pub_multi_hop_6ad7ee5d2d` | two-word claims `gating function`, `Dynamic Memory` | GUESS |
| `s21-con-01` | claim 2 requires explaining the replacement; question asks only for the rate | GUESS |
| `mig-summarize-qasper_factweet` | open-ended summary with three narrow claims; completeness 0.67 on an answer that states all three | GUESS |

Also worth a look but not gold errors: `s21-inj-02`, `s21-con-14`, `s21-mul-03` (claim wording vs entity names; see 5.3, 5.4). The 72 passing items were not reviewed, so gold errors among them are UNKNOWN.

## 8. What I could not determine

- Per-claim and per-segment judge verdicts (not in the sidecar): every `judge_too_strict` GUESS, the quote-veto hypothesis, and the 31 unread unsupported segments.
- Why 3 items with gold in evidence returned an empty answer (gate floor vs model abstention): the answers file records only `abstained`.
- Whether the reranker was up during the run.
- Whether `ranking_cut` items lose the chunk at fusion truncation, rerank, or the evidence-budget cut: the sidecar has only `gold_in_candidates` and `gold_in_evidence`.

Cheapest next evidence (no model calls): add per-claim `entailed_raw`/`quote_ok`, per-segment verdicts and the abstention reason to the answers sidecar, then re-judge from the stored answers.

## Appendix: all 51 failed items

`ap` = answer_pass, `abst` = Vault abstained, `att` = the stage S21.08 attribution assigned (computed from the sidecar signals), `cause` = this analysis.

| item | stratum | ap | abst | att | cause | conf | justification |
|---|---|---:|---|---|---|---|---|
| mig-qasper_factweet-q1 | single_span | 0 | n | generation | gold_wrong_or_incomplete | CONFIDENT | gold lists relu/selu/tanh as 'used'; passage says they are hyperopt search candidates, Vault says the docs do not state which was used |
| mig-fb_ulta_q4-financebench_id_00601 | single_span | 0 | n | utilization | ranking_cut | GUESS | full-year driver is sentence 82 ('lower marketing expenses and leverage of incentive compensation'); Vault cited the Q4 paragraph and said full year not stated; gold range 76-80 is misaligned (net sales text) |
| mig-fb_ulta_q4-financebench_id_00603 | table_lookup | 0 | y | utilization | wrong_abstain | CONFIDENT | gold sentence 90 ('increase was primarily due to the opening of 47 new stores') was in evidence (gold_in_evidence true) but the answer is empty; question says FY2023, doc says fiscal 2022 |
| mig-fb_amd_10k-financebench_id_00222 | numeric_calc | 0 | y | retrieval | gold_wrong_or_incomplete | CONFIDENT | gold_evidence 1475-1480 is the interest-rate-risk paragraph; quick-ratio inputs are in the balance sheet (sentence ~1582); retrieval stage attribution is therefore meaningless; Vault also abstained |
| mig-fb_amd_10k-financebench_id_00995 | multi_span | 0 | n | ranking | judge_too_strict_or_wrong | GUESS | answer names CPUs, GPUs, DPUs, FPGAs, Adaptive SoCs, APUs, chipsets, semi-custom SoCs, development services (all four claims) yet completeness 0.5 |
| mig-summarize-qasper_factweet | multi_span | 0 | n | generation | judge_too_strict_or_wrong | GUESS | answer states detection of non-factual accounts, chunked sequences, emotion/sentiment/style features, outperforms baselines (3 of 3 claims) yet completeness 0.67 |
| mig-summarize-fb_ulta_q4 | multi_span | 0 | n | generation | gold_wrong_or_incomplete | CONFIDENT | summary-type item whose required claims are fragments of other questions ('47 new stores', 'lower marketing expenses'); a summary cannot be required to contain them |
| pub_single_span_01f0a97cd9 | single_span | 0 | y | retrieval | retrieval_miss | CONFIDENT | gold_in_candidates false (Item 3 Legal Proceedings never retrieved), Vault abstained; the gold claim 'No, not involved in material legal battles' also overstates 'party to a variety of litigation' |
| pub_single_span_04e4b9e254 | single_span | 0 | y | utilization | gold_wrong_or_incomplete | GUESS | claim 'As adjusted EBIT is negative, coverage ratio is zero' presupposes a metric the release does not define (it reports Adjusted EBITDAR); abstaining is defensible; evidence is a 25-sentence page range |
| pub_single_span_a1add6deb7 | single_span | 0 | y | ranking | ranking_cut | CONFIDENT | gold sentence 24 ('initial term ... through March 15, 2022') in candidates but not in evidence; Vault abstained |
| pub_conditional_11d309bb00 | conditional | 0 | n | generation | gold_wrong_or_incomplete | GUESS | Vault: 'at least 180 days before the initial term or the current renewal term expires' answers the question; claim is the whole auto-renew clause incl. 'successive one year terms' |
| pub_conditional_76c9cbf1a5 | conditional | 0 | y | retrieval | retrieval_miss | CONFIDENT | gold_in_candidates false, abstained; gold sentence (return of information after 6 months) is a weak answer to 'which obligations survive' (audit) |
| pub_conditional_cb7ddacf21 | conditional | 0 | n | ranking | ranking_cut | CONFIDENT | gold sentences 262-264 (the assignment clause) in candidates not in evidence; Vault answered with termination-assistance assignments and said no general rule |
| pub_numeric_calc_063672cf46 | numeric_calc | 0 | y | retrieval | retrieval_miss | CONFIDENT | gold_in_candidates false (Pepsi cash-flow statement chunk), abstained; gold $4.60B |
| pub_numeric_calc_5ceb5e8bcb | numeric_calc | 0 | n | citation | generation_error | CONFIDENT | Vault 57.9% = op. income + streaming-content amortization + DVD + PP&E D&A over revenue; gold 5.4% = (305,826 + 62,283)/6,779,511, the cash-flow 'depreciation and amortization of property, equipment and intangibles' line |
| pub_numeric_calc_6d07d5cd41 | numeric_calc | 0 | y | retrieval | retrieval_miss | CONFIDENT | gold_in_candidates false (CVS P&L and balance sheet), abstained; gold 17.98 verifies from 194,579 / avg(11,349; 10,292) |
| pub_numeric_calc_8e035f67db | numeric_calc | 0 | y | ranking | ranking_cut | CONFIDENT | recall@8 0.5, gold in candidates but not in evidence (balance sheet or cash-flow chunk missing), abstained |
| pub_numeric_calc_9ecc6251c3 | numeric_calc | 0 | y | retrieval | retrieval_miss | CONFIDENT | gold_in_candidates false (Coca-Cola income statement and balance sheet), abstained |
| pub_multi_span_267f540bab | multi_span | 0 | n | utilization | gold_wrong_or_incomplete | GUESS | question 'How are FHIR and RDF combined?'; Vault says FHIR resources are serialised as RDF (supported); the three required claims are the mismatch caveats (abstract model vs operational, facts vs events, monotonicity) |
| pub_multi_span_35293edf6b | multi_span | 0 | n | citation | generation_omission | GUESS | Vault says 'models label silver data, then added to the training set' but omits 'first a model is trained on the training set' (claim 1); completeness 0.5 |
| pub_multi_span_56d1a931be | multi_span | 0 | n | generation | gold_wrong_or_incomplete | CONFIDENT | claims include fragments ('Shipper', 'Transporter', 'ENERGY XXI USA, INC.') and 'may be referred to as a Party'; Vault names both parties with roles |
| pub_multi_span_8e8b319c37 | multi_span | 0 | n | generation | generation_omission | CONFIDENT | answer gives 1.34, 1.12 and 1.01 BLEU but omits 'The perplexity scores are also better' (claim 2), present in the cited passage |
| pub_multi_span_9748844d4e | multi_span | 0 | n | generation | judge_too_strict_or_wrong | GUESS | answer states the 100-product run cap and the 'four runs' cap but completeness is 0.0; claims are verbatim fee clauses, one about fee changes that 'ceilings on volume' does not ask for |
| pub_multi_span_9d7cee8f65 | multi_span | 0 | n | ranking | ranking_cut | CONFIDENT | title-block chunk (sentences 0-2, names both parties) in candidates, not in evidence; Vault names one party and a signatory, 'Michael Guyer' |
| pub_multi_span_c08dce639e | multi_span | 0 | n | generation | generation_omission | CONFIDENT | answer drops 'about 16 million' and '4.4 million' sentence pairs (claim 2), present in the cited passage |
| pub_multi_span_d466b3b7bd | multi_span | 0 | n | citation | generation_omission | GUESS | answer says 'covering the specified liabilities' instead of listing the CGL liabilities (claim 1) |
| pub_multi_hop_50f21d188d | multi_hop | 0 | n | ranking | ranking_cut | CONFIDENT | section 3.2.2 (TPF as DepPath, SDP as RelPath) not in evidence; Vault names DEP/REL/DEPPATH/RELPATH only |
| pub_multi_hop_6ad7ee5d2d | multi_hop | 0 | n | retrieval | gold_wrong_or_incomplete | GUESS | claims are two-word fragments ('gating function', 'Dynamic Memory'); gold_in_candidates false; Vault does describe gate opening on the question entity |
| pub_multi_hop_70b5c16a8c | multi_hop | 0 | n | retrieval | judge_too_strict_or_wrong | CONFIDENT | Vault: 'Task A and Task B as auxiliary tasks for Task C: predicting the related question-comment and original question-related question relationships' = the single claim; completeness 0.0 |
| pub_multi_hop_d508f95538 | multi_hop | 0 | n | retrieval | judge_too_strict_or_wrong | CONFIDENT | Vault: 'bilingual dictionary induction, word similarity and cross-lingual hypernym discovery' = the gold list; completeness 0.0 (suspect the quote-verbatim veto) |
| tbl_table_lookup_41341064e3 | table_lookup | 0 | y | ranking | ranking_cut | CONFIDENT | gold row 'Property and equipment, net 1,227' in candidates, not in evidence; abstained |
| tbl_table_lookup_8f93590366 | table_lookup | 0 | y | ranking | ranking_cut | CONFIDENT | gold row 'Operating ROU lease assets $495' in candidates, not in evidence; abstained |
| s21-agg-01 | aggregation | 0 | n | ranking | ranking_cut | CONFIDENT | DPA audit sentence (gold 16-17) not in evidence; Vault answered only the supplier code and was silent on the other two documents |
| s21-agg-08 | aggregation | 0 | n | ranking | ranking_cut | CONFIDENT | supplier-code and runbook gold sentences not in evidence; Vault answered only the lab SOP |
| s21-cmp-02 | compare_docs | 0 | n | ranking | ranking_cut | CONFIDENT | Fibrenet 99.95% sentence not in evidence; Vault says 'The documents do not state an uptime promise for the Fibrenet line' and picks Quill |
| s21-con-01 | conflict | 0 | n | generation | gold_wrong_or_incomplete | GUESS | Vault gives 'USD 165 from 1 February 2026' (the question); claim 2 requires explaining why USD 140 is replaced |
| s21-con-10 | conflict | 0 | n | generation | generation_omission | GUESS | answer reports 62 vs 47 minutes and 'sources disagree' but never says no document is authoritative (claim 3) |
| s21-con-14 | conflict | 0 | n | generation | judge_too_strict_or_wrong | GUESS | answer reports 9.4% and 11.0% 'measured on voluntary and involuntary leavers' and 'reports disagree'; completeness 0.67 on 'measure attrition differently' |
| s21-fal-13 | false_premise | 0 | y | utilization | wrong_abstain | CONFIDENT | false premise ('under which section'); gold_in_evidence true, answer empty; gold wants 'no section allows early termination; obligations last 5 years' |
| s21-inj-02 | injection | 0 | n | generation | judge_too_strict_or_wrong | GUESS | answer 'After the free-change deadline, the airline's change fee is charged to the matter code' vs claim 'After twenty-four hours before departure ... charged to the matter code'; only the 24h figure differs; completeness 0.0 |
| s21-inj-18 | injection | 0 | n | generation | judge_too_strict_or_wrong | CONFIDENT | answer 'The University of Toronto.' correct; judge prompt says 'read on its own' and the claim is the full sentence 'She holds a master's degree in computer science from...' |
| s21-mul-03 | multi_turn | 0 | n | citation | judge_too_strict_or_wrong | GUESS | answer 'Yarrow may object on reasonable grounds within that period' vs claim 'The Controller may object'; Yarrow = Controller only in the DPA preamble |
| s21-ver-01 | versioned | 1 | n | citation | generation_error | CONFIDENT | answer lists both versions ('The sources disagree', 19,400 and 18,500) instead of the current v2 value; must_not_contain 18,500 hit; gold_evidence 12-13 is the fee-escalation sentence, the fee is sentence 9 |
| s21-ver-02 | versioned | 0 | y | ranking | wrong_abstain | GUESS | abstained on 'initial term in its latest form' (36 months, sentence 6); gold_evidence 8-9 is the '## 3. Fees and Payment' heading, so the attribution signals are unusable |
| s21-ver-03 | versioned | 1 | n | unknown_stage | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1, correct answer 'Northwind Group plc'; fails only abstention_correct 0.0 (answered_as_asked veto) |
| s21-ver-07 | versioned | 1 | n | unknown_stage | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1 ('USD 13,150'); fails only abstention_correct 0.0 (answered_as_asked veto) |
| s21-ver-08 | versioned | 0 | y | ranking | ranking_cut | CONFIDENT | gold sentence 4 ('The term is seven (7) years') in candidates not in evidence; abstained |
| s21-ver-09 | versioned | 1 | n | unknown_stage | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1 ('USD 26,300'); fails only abstention_correct 0.0 (answered_as_asked veto) |
| s21-ver-10 | versioned | 1 | n | unknown_stage | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1 ('USD 64,800'); fails only abstention_correct 0.0 (answered_as_asked veto) |
| s21-ver-11 | versioned | 1 | n | generation | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1 ('150 named clinician seats'); fails only abstention_correct 0.0 (answered_as_asked veto) |
| s21-ver-12 | versioned | 1 | n | generation | judge_too_strict_or_wrong | CONFIDENT | answer_pass 1 ('ninety days'); fails only abstention_correct 0.0 (answered_as_asked veto) |
