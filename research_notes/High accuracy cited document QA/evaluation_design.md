# Credible evaluation for document-QA / RAG: golden sets and LLM-as-judge (2023-2026)

Context for the writer: Vault's current eval has 122 golden items (CUAD, QASPER, FinanceBench, authored), gold status 'draft', binary completeness and faithfulness judges from the same vendor family as the generator, a completeness judge that wrongly failed at least 2 of 5 sampled failures, and CIs of about +/-14 points on 44 items. Confidence tags: VERIFIED = read in a primary source this session; CLAIMED = from a search summary or secondary source; INFERENCE = my own derivation.

## How strong teams build golden sets (sources, sizes, stratification, edge-case taxonomies, adversarial generation, human review)

### Takeaway
Credible RAG benchmarks stratify on an explicit question taxonomy (CRAG: 8 types, including 12% false-premise; plus dynamism and entity popularity). They plant hard negatives such as same-document distractors and contradictory answers, and they score hallucination worse than abstention. Published benchmarks also carry label errors: Vendrow et al. trace more than half of model failures on most benchmarks they studied to label noise. A 'draft' gold set should therefore be treated as a source of judge and score error until it has been audited.

### Cited Findings
- **CRAG (Meta, 2024).** 4,409 QA pairs across 5 domains (Finance, Sports, Music, Movie, Open), with about 220K webpages, a 2.6M-entity mock KG and 38 mock APIs. VERIFIED. — [CRAG paper](https://arxiv.org/html/2406.04744)
  - Question-type mix: Simple 27%, Simple-with-condition 16%, Set 9%, Comparison 12%, Aggregation 11%, Multi-hop 9%, Post-processing-heavy 4%, False premise 12%. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
  - Second axis, dynamism: real-time 10%, fast-changing 13%, slow-changing 23%, static 54%. Third axis, popularity: 661 head, 658 torso and 665 tail KG questions. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
  - Scoring: Perfect = 1, Acceptable = 0.5, Missing ("I don't know") = 0, Incorrect = -1. Truthfulness = accuracy minus hallucination rate. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
  - Headline results: LLM-only reached at most 34% accuracy. Straightforward RAG with GPT-4 Turbo reached 43.6% accuracy with 30.1% hallucination. The best industry system reached 62.6% perfect with 17.9% hallucination, and industry systems ranged from 16.0% to 25.3% hallucination. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
  - The largest drops are on set, aggregation, false-premise and post-processing questions. CLAIMED. — [CRAG review summary](https://www.themoonlight.io/review/crag-comprehensive-rag-benchmark)
- **FinanceBench (Patronus, 2023).** 10,231 questions about public companies, each with an answer and an evidence string. CLAIMED. — [arXiv 2311.11944](https://arxiv.org/abs/2311.11944v1)
  - GPT-4-Turbo with a retrieval system answered incorrectly or refused on 81% of questions. This was measured on a 150-case sample, not the full set: a precedent for small human-graded subsets. CLAIMED. — [Patronus docs](https://docs.patronus.ai/docs/financebench-1); [arXiv](https://arxiv.org/abs/2311.11944v1)
  - Other settings: closed-book 11%, long-context 79%, oracle 85%. CLAIMED. — [arXiv](https://arxiv.org/abs/2311.11944v1)
- **FRAMES (Google, 2024).** 824 questions, each needing 2-15 Wikipedia articles. Gemini-Pro-1.5 scored 40.8% with naive prompting, 47.4% with BM25 (4 docs), 72.9% with oracle retrieval and 0.66 with multi-step retrieval. CLAIMED. — [HF dataset card](https://huggingface.co/datasets/google/frames-benchmark); [arXiv 2409.12941](https://arxiv.org/html/2409.12941v1)
- **LegalBench-RAG (2024).** 6,858 queries over a corpus of about 79M characters. It is retrieval-focused: it traces LegalBench contexts back to their exact spans in the source documents, including CUAD-derived contracts. CLAIMED. — [arXiv 2408.10343](https://arxiv.org/html/2408.10343v1)
- **RAGTruth (ACL 2024).** About 18,000 naturally generated RAG responses from several LLMs, with human span-level (word-level) hallucination labels plus intensity ratings. A small model fine-tuned on it is competitive with GPT-4 prompt-based detection. CLAIMED. — [arXiv 2401.00396](https://arxiv.org/pdf/2401.00396)
  - A third-party page reports 17,790 responses with 43.1% containing at least one hallucination. Not verified against the paper. — [alphaxiv](https://www.alphaxiv.org/benchmarks/university-of-illinois-at-urbana-champaign/ragtruth)
- **ARES adversarial negatives.** Synthetic queries are kept only if the retriever ranks their source passage first. Negatives come in two strengths:
  - weak: random passages or answers;
  - strong: same-document passages, BM25-retrieved passages, or contradictory answers.
  Positives and negatives are generated in equal numbers. VERIFIED. — [ARES](https://arxiv.org/html/2311.09476v2)
- **Label noise is common (Vendrow et al., MIT, 2025).** The authors re-labeled 15 popular benchmarks and report that "for the majority of benchmarks we investigate, more than half of model failures can be attributed to label noise". They cite about 5% errors in GSM8K. CLAIMED. — [arXiv 2502.03461](https://arxiv.org/html/2502.03461v1)
  - GSM8K-Platinum: of 1,319 questions, models flagged 219. Of those, 110 were rejected as ambiguous or ill-posed, 10 were re-labeled and 99 were verified, leaving 1,209. CLAIMED. — [GSM8K-Platinum card](https://huggingface.co/datasets/madrylab/gsm8k-platinum/blob/main/README.md)
  - The method: run several strong models, send every item any model fails to human review, then fix the label or drop the item.
- **Hamel Husain on building an eval set.** Build the dataset along dimensions (features, scenarios, personas) from real and synthetic interactions. To find failure modes: "start with around 30 examples and keep going until I do not see any new failure modes." VERIFIED. — [hamel.dev llm-judge](https://hamel.dev/blog/posts/llm-judge/)

### Inferences
- A taxonomy for Vault (INFERENCE, adapted from CRAG and ARES to private documents). One gold item per row as a minimum, ideally 20 or more per cell for reporting:
  - answerable-single-span
  - answerable-with-condition
  - multi-span or set (CUAD clause lists)
  - comparison or aggregation across documents
  - numeric and table reasoning (FinanceBench)
  - multi-hop within a document (QASPER)
  - unanswerable: absent from the corpus
  - false premise
  - distractor or near-duplicate document (e.g., two versions of the same contract)
  - contradiction across documents
  - prompt injection embedded in the document text
  - formatting long tail (footnotes, multi-column, scanned/OCR)
  - multilingual
- Hallucination should cost more than abstention, following CRAG's -1/0/+1 scheme. Report truthfulness = correct - hallucinated alongside accuracy.
- The 2-of-5 false-fail finding fits the Vendrow pattern: before tuning the system, audit every item the system "fails" and classify each failure as a system error, a gold error or a judge error.

### Gaps
- No primary-source taxonomy found for prompt-injection-in-document, versioned-document or OCR-noise items in RAG QA benchmarks specifically.
- Databricks, Eugene Yan, OpenAI and Anthropic golden-set guidance was not fetched this session. Their recommended sizes and stratification are not captured here.
- Multilingual RAG benchmarks (e.g., MIRACL-based) were not researched.

## Dataset size vs statistical power (CI width, paired tests, per-category reporting)

### Takeaway
At about 70% accuracy, a +/-5-point 95% CI needs about 320 independent items and +/-10 points needs about 80. Two methods tighten this. Paired per-item differences between system variants remove question-difficulty variance. Clustered standard errors correct for items that share a source document, and they can be more than three times larger than naive SEs. On the judge side, ARES-style prediction-powered inference (PPI) needs about 150 or more human labels.

### Cited Findings
- **Report the SEM and use the CLT.** The 95% CI is mean +/- 1.96 x SEM. VERIFIED. — [Anthropic, statistical approach to evals](https://www.anthropic.com/engineering/statistical-approach-to-model-evals)
- **Cluster standard errors.** Cluster on the unit of randomization, such as a passage. "clustered standard errors on popular evals can be over three times as large as naive standard errors." VERIFIED. — [Anthropic](https://www.anthropic.com/engineering/statistical-approach-to-model-evals); [arXiv 2411.00640](https://arxiv.org/html/2411.00640v1)
- **Use paired differences.** Comparing two models on the same questions with a paired test is a "free" variance reduction. Per-question score correlation between frontier models is 0.3 to 0.7. Report the mean difference, SE, CI and correlation. VERIFIED. — [Anthropic](https://www.anthropic.com/engineering/statistical-approach-to-model-evals)
- **Reduce within-question variance** by resampling answers several times per question and averaging. VERIFIED. — [Anthropic](https://www.anthropic.com/engineering/statistical-approach-to-model-evals)
- **Power analysis.** The paper gives a sample-size formula for detecting a given difference. It is not on the blog page. VERIFIED (pointer only). — [Anthropic](https://www.anthropic.com/engineering/statistical-approach-to-model-evals)
- **Judge-validation sample sizes (Husain).**
  - "Below 60 examples, the confidence intervals are often too wide to support a useful conclusion."
  - "Aim for about 100 examples per failure mode"
  - "30 to 50 examples of each label in each set" VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **PPI (ARES).**
  - About 150 or more human-labelled datapoints are needed. Below about 100-150, ARES could not reliably separate systems. The main experiments used 300.
  - PPI 95% CIs averaged 7.4 points wide for context relevance and 6.1 points for answer relevance. They covered the truth more than 95% of the time.
  - ARES needed 78% fewer annotations than per-system sampled annotation (150 per system, 1,350 total). VERIFIED. — [ARES](https://arxiv.org/html/2311.09476v2)

### Inferences
- **Half-widths from the binomial normal approximation** (INFERENCE, my calculation; regenerate with `python3 -c "import math;print(1.96*math.sqrt(.7*.3/44))"`):

  | n | p=0.5 | p=0.7 | p=0.8 |
  |---|---|---|---|
  | 44 | 14.8 | 13.5 | 11.8 |
  | 122 | 8.9 | 8.1 | 7.1 |
  | 200 | 6.9 | 6.4 | 5.5 |
  | 300 | 5.7 | 5.2 | 4.5 |
  | 400 | 4.9 | 4.5 | 3.9 |

  This matches Vault's observed +/-14 on 44 items.
- **Items needed for a given half-width:** about 81 for +/-10, 165 for +/-7 and 323 for +/-5 at p=0.7; 97, 196 and 385 at p=0.5. These counts are for independent items. Several questions per CUAD contract or QASPER paper form a cluster, which widens the CIs, so Vault should cluster SEs by source document.
- **Absolute accuracy on 122 items has about +/-8 points of uncertainty.** Use paired tests (per-item McNemar or a paired bootstrap) for change decisions. This is consistent with Vault's existing paired-test convergence engine.
- **Per-category reporting:** with about 20 items per stratum the CI is about +/-20 points. Strata are diagnostic, not decision-grade, unless pooled or enlarged.

### Gaps
- The exact power formula from arXiv 2411.00640 was not extracted.
- No source found giving recommended per-category minimum counts for RAG specifically.

## Synthetic question generation pitfalls and gold-answer auditing

### Takeaway
Synthetic generation tends to produce lexically easy, single-passage questions. ARES counters this by filtering to questions whose source passage the retriever ranks first, and by generating strong negatives. Gold answers need an independent audit. The most efficient audit trigger is "a strong model disagrees with gold", followed by human adjudication into fix, keep or drop (Platinum method).

### Cited Findings
- ARES generates queries with few-shot FLAN-T5 XXL and keeps only those whose source passage the retriever ranks first. This is a round-trip consistency filter. VERIFIED. — [ARES](https://arxiv.org/html/2311.09476v2)
- The Platinum process flags items that models fail and has humans adjudicate them. Mislabeled items are fixed; ambiguous or ill-posed ones are removed. On GSM8K, 110 of the 219 flagged items were removed as bad questions, about half. CLAIMED. — [Vendrow et al.](https://arxiv.org/html/2502.03461v1); [GSM8K-Platinum](https://huggingface.co/datasets/madrylab/gsm8k-platinum/blob/main/README.md)
- Husain: "Fix errors found in review, then re-review". Developers acting as the domain expert, or a convenient proxy standing in, "is a recipe for disaster." VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- Criteria drift: "it is impossible to completely determine evaluation criteria prior to human judging of LLM outputs." Users went back and changed earlier grades. CLAIMED (abstract/summary level). — [Shankar et al., UIST'24](https://arxiv.org/abs/2404.12272)

### Inferences
- **Leakage risk for Vault (INFERENCE).** Questions authored or generated from the same chunk the retriever indexes overlap lexically with that chunk, which inflates retrieval recall. Mitigations: paraphrase questions away from the source wording, write questions from document-level intent rather than a chunk, and keep a held-out test split that is never used for tuning (already a Vault rule).
- **Gold-audit protocol:**
  1. Run the system and a different-vendor strong model on every item.
  2. Send all disagreements with gold to human review.
  3. Tag each one gold-wrong, gold-incomplete, ambiguous (drop) or system-wrong.
  4. Promote items from 'draft' to 'verified' only after this review.
- **Completeness false-fails.** These often arise because gold lists more than the question requires, or because gold wording differs from an equivalent answer. Store gold as atomic required claims, plus optional claims and acceptable alternates, rather than as one prose answer.

### Gaps
- No quantitative study found this session on the difficulty gap between synthetic and human-written RAG questions, or on leakage rates.

## LLM judge design (binary vs graded, reference-based vs free, claim decomposition, biases, judge choice, over-strictness, agreement targets)

### Takeaway
Evidence and practitioner consensus favor narrow binary judges, each with a written critique and validated separately against human labels. Report TPR/TNR or Cohen's kappa rather than raw agreement, which can hide judge disagreement of up to 20 points. Claim-level decomposition (RAGAS factual correctness: TP/FP/FN over claims) turns "completeness" into measurable recall against gold claims. Self-preference is real (Panickssery et al.), so use a different-family judge or an ensemble. CRAG averaged two judges from different families for this reason.

### Cited Findings
- **Binary over scales.** If evaluations rely on "a bunch of metrics that LLMs score on a 1-5 scale", the team is doing it wrong. Use pass/fail with a critique "detailed enough so that you can use it in a few-shot prompt for a LLM judge". VERIFIED (opinion of a practitioner). — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **Report TPR and TNR separately.** If errors occur in 5% of cases, an always-Pass judge has 95% agreement and catches nothing. VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **Kappa over percent agreement.** Judges with 80%+ agreement can still produce scores up to 20 points apart. Kappa: GPT-4 Turbo 0.84 and Llama-3-70B 0.79, against human-human 0.96. Judges show "a bias toward leniency" and sensitivity to prompt complexity. Setting: TriviaQA. CLAIMED. — [Thakur et al., Judging the Judges](https://arxiv.org/html/2406.12624v2)
- **MT-Bench.** GPT-4 judges agree with humans above 80%, comparable to human-human agreement. The paper documents position bias, verbosity bias (via a "repetitive list" attack) and self-enhancement bias. Mitigations: swap positions and run twice, and add few-shot examples. CLAIMED. — [Zheng et al. 2023](https://arxiv.org/abs/2306.05685)
- **Self-preference.** GPT-3.5, GPT-4 and Llama-2 rate their own summaries higher than others' even when humans rate them equal. Self-recognition strength correlates linearly with self-preference. CLAIMED. — [Panickssery et al., NeurIPS 2024](https://arxiv.org/html/2404.13076v1)
- **Cross-family averaging (CRAG).** CRAG's auto-eval does exact match first, then averages ChatGPT and Llama-3-70B judges "to reduce self-preference bias". Agreement with humans (F1): 94.7% for ChatGPT and 98.9% for Llama 3. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
- **RAGAS answer correctness and factual correctness.** Claims are split into TP (in response and reference), FP (in response only) and FN (in reference only). Precision = TP/(TP+FP), recall = TP/(TP+FN). Answer correctness = 0.75 x the F1-style factual score |TP|/(|TP| + 0.5(|FP|+|FN|)) + 0.25 x semantic similarity. CLAIMED (docs). — [RAGAS factual correctness](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/factual_correctness/); [RAGAS answer correctness](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/answer_correctness/); [telecom paper quoting the implementation](https://arxiv.org/pdf/2407.12873)
- **ARES judges.** Fine-tuned DeBERTa-v3-Large binary classifiers, one per metric. They beat RAGAS on Kendall tau for real systems by 0.16 (context relevance) and 0.15 (answer relevance). VERIFIED. — [ARES](https://arxiv.org/html/2311.09476v2)
- **Judge model choice (Husain).** Use "the most powerful model I can afford in my cost/latency budget"; "I prefer not to fine-tune LLM judges." VERIFIED (opinion). — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)

### Inferences
- **Fixing the over-strict completeness judge (INFERENCE):**
  1. Make it reference-based at claim level. Gold stores required claims; the judge checks each one ("is required claim k entailed by the answer? yes/no"). Completeness = recall over required claims, with extra correct detail not penalized.
  2. Instruct the judge explicitly to accept paraphrase, equivalent numeric formats and unit conversions.
  3. Validate it against human labels with TPR/TNR. The current judge's TNR on truly-correct answers is the number that looks broken.
- **Faithfulness: largely avoid the LLM judge.** Vault already has application-managed citations. Faithfulness can be checked mostly deterministically per cited sentence (does handle-resolved `cited_text` support the claim?), with the LLM or NLI step restricted to entailment of one claim against one cited sentence.
- **Same-vendor risk.** Given self-preference, either use a different-family judge, such as the dormant Anthropic adapter, or average two families as CRAG does. Measure the gap on a human-labelled set rather than assuming it.

### Gaps
- FActScore, G-Eval, TruLens and DeepEval specifics were not fetched this session. From background knowledge (not re-verified), FActScore decomposes generations into atomic facts and scores the fraction supported by a knowledge source, and G-Eval uses CoT plus form-filling with probability-weighted scores. Verify both before citing.
- The RAGAS faithfulness formula (supported claims / total claims) was not confirmed from the docs this session.
- I found no published numeric "target kappa" standard for RAG judges. Practitioner targets seen: more than 90% agreement (Husain, Honeycomb case); CRAG's 94.7-98.9% F1; Thakur's best judges at kappa about 0.8.

## Human-label calibration workflow (how many labels, IAA, aligning judges, recalibration cadence)

### Takeaway
Use one principal domain expert who labels pass/fail with critiques. Split the labels into train (10-20%, used as few-shot examples), dev (40-45%, for iterating) and a frozen test set (40-45%, run once). Iterate until TPR and TNR are both high (more than 90% agreement in about 3 rounds in Husain's case). Re-run whenever the generator, judge or prompt changes, because criteria drift is expected.

### Cited Findings
- **Critique-shadowing steps:**
  1. Find the principal domain expert.
  2. Build a diverse dataset.
  3. Have the expert label pass/fail with critiques.
  4. Fix errors and re-review.
  5. Build the judge from expert examples and iterate against the expert's labels.
  6. Run error analysis on unseen data and add specialized judges if needed.

  Honeycomb reached ">90%" agreement in "only three iterations". VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **Data splits.** "10 to 20 percent of the labeled examples for training", 40-45% for dev and 40-45% for test. "Dev set examples must stay out of the prompt." "Freeze the prompt, run the judge on the test set once." VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **Cadence.** Run human review "at regular intervals and whenever something material changes". After a model update, "You have to go through the process again". After the first two iterations, oversample errors while keeping some random sampling. VERIFIED. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- **EvalGen.** The LLM proposes criteria and candidate assertions; humans grade a subset; the implementations that best match human grades are selected. Criteria drift means grading helps define the criteria. CLAIMED. — [Shankar et al.](https://arxiv.org/abs/2404.12272)
- **PPI (ARES).** A small human set corrects the judge's bias across a large judge-scored set and yields valid CIs. Needs at least about 150 labels. VERIFIED. — [ARES](https://arxiv.org/html/2311.09476v2)
- **Inter-annotator agreement.** Human-human kappa of 0.96 on TriviaQA sets the upper reference for judge kappa. CLAIMED. — [Thakur et al.](https://arxiv.org/html/2406.12624v2)

### Inferences
- **A concrete plan for Vault (INFERENCE):**
  - Label 150-200 (question, answer) pairs per judge, balanced at 30-50 or more per class in each of dev and test.
  - Double-label about 20% with a second annotator to measure kappa.
  - Report judge TPR/TNR and kappa with CIs.
  - Accept a judge only when its test-set kappa is at least about 0.7-0.8, near the best judges in Thakur et al.
  - Re-validate on any change to the generator model, judge model or prompt version (Vault already bumps prompt versions).
- **Use PPI for headline numbers.** This turns a 150-label human set plus judge scores on all items into CIs that are corrected for judge bias.

### Gaps
- No source found giving a recommended second-annotator fraction or Krippendorff alpha thresholds for RAG labelling.

## Metrics that matter for cited QA (correctness vs completeness, claim-level P/R, citation P/R, abstention)

### Takeaway
Separate four quantities:
1. Correctness (claim precision against gold or evidence).
2. Completeness (recall of required gold claims).
3. Citation quality (ALCE citation recall per statement and citation precision per citation).
4. Abstention (answer-rate versus hallucination, scored as in CRAG where a wrong answer costs more than "I don't know").

### Cited Findings
- **ALCE citation metrics.**
  - Citation recall is per statement: a statement passes if its cited passages, checked by NLI, fully support it.
  - Citation precision is per citation: a citation is irrelevant if it cannot support the statement alone and does not help the other citations support it.
  - Agreement with humans: 85.1% for recall and 77.6% for precision. The weaker precision is attributed to NLI missing partial support. CLAIMED. — [ALCE, Gao et al. EMNLP 2023](https://arxiv.org/pdf/2305.14627); [definitions restated in 2404.03381](https://arxiv.org/pdf/2404.03381)
- **Critique of ALCE precision.** A later paper argues ALCE precision penalizes extra useful citations. It redefines precision to allow comprehensive citation and excludes statements that need no citation from recall. CLAIMED. — [arXiv 2410.11217](https://arxiv.org/html/2410.11217v1)
- **Claim-level P/R against a reference:** see RAGAS factual correctness, TP/(TP+FP) and TP/(TP+FN). CLAIMED. — [RAGAS](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/factual_correctness/)
- **Abstention scoring:** CRAG's 1 / 0.5 / 0 / -1 scheme and truthfulness = accuracy - hallucination. VERIFIED. — [CRAG](https://arxiv.org/html/2406.04744)
- **Retrieval-only evaluation with span-level gold:** precision and recall of retrieved snippets against the annotated spans, as in LegalBench-RAG. CLAIMED. — [LegalBench-RAG](https://arxiv.org/html/2408.10343v1)

### Inferences
- **Proposed metric set for Vault (INFERENCE):**
  - answer accuracy (CRAG-style, with "acceptable" partial credit)
  - required-claim recall (completeness)
  - unsupported-claim rate (faithfulness, per claim)
  - citation recall (each claim has at least one handle whose `cited_text` entails it)
  - citation precision (each handle is necessary or helpful)
  - on unanswerable and false-premise strata: abstention precision and recall (correct refusal rate vs false-refusal rate on answerable items)
  - truthfulness = correct - hallucinated
- **Report every metric per stratum** with clustered CIs. Make change decisions on paired differences.

### Gaps
- I found no consensus definition of "abstention precision/recall" in a primary RAG source this session. The CRAG scoring is the closest verified standard.
- Vendor guidance pages (OpenAI evals, Anthropic eval docs, Databricks, Braintrust, Langfuse) were not fetched because of the tool-call budget. Their specific recommendations are not represented here.
