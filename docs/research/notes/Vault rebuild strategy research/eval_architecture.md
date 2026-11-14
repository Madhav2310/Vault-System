# Credible, cheap RAG evaluation (A) and modular, explainable, observable LLM architecture (B), 2024-2026

Scope note: research done 2026-11-13 with about 20 search/fetch calls. Several primary sources returned only abstracts, so some numbers are second-hand. Each finding says where it came from. Items marked (secondary) come from aggregators or blogs, not the original paper or docs.

## A1. What RAG eval tools (RAGAS, ARES, DeepEval, TruLens, Phoenix, OpenAI Evals/Graders, promptfoo) compute, and how reliable they are

### Takeaway
Framework metrics such as faithfulness, answer relevance and context precision/recall are LLM-judge prompts. They correlate reasonably with humans on the authors' own benchmarks. But implementations of the "same" metric disagree with each other, scores drift across library versions, and high correlation does not mean failures get caught. Treat them as uncalibrated heuristics unless you validate them against your own labels. Prefer deterministic checks wherever the property can be computed, such as citation-handle validity or exact answers.

### Cited Findings
- RAGAS (Es et al., 2023) defines reference-free faithfulness, answer relevance and context relevance using LLM prompts, validated on its own WikiEval set. The authors report that predictions "closely aligned with human predictions, especially for faithfulness and answer relevance" — [RAGAS paper](https://arxiv.org/html/2309.15217v1)
- Reported WikiEval agreement with human annotators: faithfulness 0.95, answer relevance 0.78, context relevance 0.70 (secondary summary, not re-checked against the paper's table) — [RAGAS paper](https://arxiv.org/html/2309.15217v1) via search summary
- ARES reports Kendall's tau of about 0.91 (context relevance) and 0.97 (answer relevance) in-domain between judge-based and human system rankings (secondary; the original was not opened). Note that this is a *ranking* correlation across systems, not per-item accuracy — search summary referencing ARES; see [RAGChecker](https://arxiv.org/pdf/2408.08067) for a comparison that uses ARES as a baseline
- RAGChecker (NeurIPS 2024 Datasets and Benchmarks) built its own human-preference set. It reports the strongest correlation with human preference among the frameworks it compared, with RAGAS and ARES as baselines. It computes claim-level precision/recall diagnostics for retriever and generator separately — [RAGChecker](https://proceedings.neurips.cc/paper_files/paper/2024/file/27245589131d17368cccdfa990cbf16e-Paper-Datasets_and_Benchmarks_Track.pdf)
- GroUSE (v3, Jan 2025) has 144 unit tests across 7 generator failure modes. Existing automated RAG eval frameworks "often overlook important failure modes, even when using GPT-4 as a judge". Correlation with GPT-4 is "an incomplete proxy" for judge quality and should be supplemented with unit tests for specific failure modes — [GroUSE](https://arxiv.org/abs/2409.06595)
- GroUSE: RAGAS and DeepEval define faithfulness similarly, but their results "differ significantly due to differences in the prompts used in their respective implementations" — [GroUSE PDF](https://arxiv.org/pdf/2409.06595)
- Practitioner test (anecdotal, 5 runs per judge at temperature 0): the same fabricated answer scored 0.0 on RAGAS and 1.0 on DeepEval. RAGAS asks whether every claim is *supported* by context, while DeepEval asks whether any claim *contradicts* it, so a figure the context omits passes DeepEval — [DEV Community](https://dev.to/nickjlamb/we-gave-the-same-fabricated-answer-to-ragas-and-deepeval-one-scored-it-00-the-other-scored-it-10-57om)
- Telecom-domain study: RAGAS faithfulness and answer correctness aligned best with expert judgment. Other RAGAS metrics aligned less well — [Roychowdhury et al. 2024](https://arxiv.org/pdf/2407.12873)
- Context precision/recall are reference-based (they need ground-truth context or answer), while faithfulness is reference-free. Metric prompts change between releases, so pin the library version (secondary FAQ) — [Ragas FAQ](https://ai-qa-learning-library.pages.dev/ragas-faq)
- A crowdsourcing study found reference-based lexical metrics (BLEU, BERTScore) have low correlation with human RAG utility judgments across dimensions — [Viability of Crowdsourcing for RAG Evaluation](https://arxiv.org/pdf/2504.15689)
- OpenAI graders: `string_check` returns 0 or 1. `text_similarity` scores against a reference (BLEU, ROUGE, METEOR, cosine). `score_model` has a model return a numeric score in a range. `python` runs a custom `grade(sample, item)` function. The guide also says: "OpenAI is deprecating graders as part of the evals and fine-tuning workflows", and points to the deprecations page for timelines — [OpenAI graders guide](https://developers.openai.com/api/docs/guides/graders)
- Azure's mirror of the grader docs also lists `label_model`, which requires a model that supports structured outputs — [Azure OpenAI graders](https://learn.microsoft.com/en-au/Azure/foundry/concepts/evaluation-evaluators/azure-openai-graders)

### Inferences
- Do not build Vault's eval around the OpenAI hosted Evals/Graders API, because it is marked deprecated. Use local graders, such as pytest plus your own judge prompts through `app.llm.gateway`, so evals survive vendor changes.
- Vault already uses application-managed citation handles. That makes "citation validity" (the handle resolves and the cited sentence exists) and "quote exactness" deterministic, so no judge is needed. This is stronger evidence than any RAGAS faithfulness score and costs nothing.
- If you report a framework metric at all, report the exact library version and judge model, and give per-item agreement with your own human labels. A bare RAGAS number is not credible to a careful reviewer, given the GroUSE and cross-implementation findings.
- A cheap and more defensible faithfulness check is binary and claim-level: "is every claim supported by the cited sentences?", calibrated on about 30-50 human-labelled items. This mirrors RAGAS's "supported" framing, which catches omissions-as-fabrications.

### Gaps
- Did not verify TruLens (RAG triad), Arize Phoenix evals, DeepEval or promptfoo metric definitions or published human-correlation numbers from their own docs. No calls were left.
- Did not open the ARES paper (Saad-Falcon et al.) to confirm its PPI-based confidence intervals and exact tau values.
- No single head-to-head benchmark ranking all frameworks on the same human-labelled data was found. RAGChecker comes closest but uses its own dataset.

## A2. LLM-as-judge pitfalls and mitigations

### Takeaway
Judges have measurable position, verbosity, leniency and model-family biases. Percent agreement hides score disagreement, and aggregate agreement can mask failure on hard slices. The evidence-backed mitigations are:
- binary, criterion-specific, reference-guided judgments
- calibration against a small set of expert labels, reported with kappa or TPR/TNR rather than raw agreement
- order swapping for pairwise comparisons
- a judge from a different family, or at least a disclosed same-vendor risk

### Cited Findings
- MT-Bench / Chatbot Arena (Zheng et al., 2023): GPT-4 as judge reached over 80% agreement with human preferences, "comparable to reported agreement between human annotators". The paper identified position bias, with all tested judges showing strong position bias and most favouring the first position, as well as verbosity bias and possible self-enhancement bias. On self-enhancement, the authors said "our study cannot determine whether the models exhibit a self-enhancement bias" because data were limited — [Zheng et al. 2023](https://arxiv.org/pdf/2306.05685)
- "Judging the Judges: Evaluating Alignment and Vulnerabilities in LLMs-as-Judges" (Thakur et al., 2024) evaluated 13 judges and 9 exam-taker models. Only the best and largest judges reach "reasonable alignment with humans", still below inter-human agreement. Judge scores can differ from human scores by up to 5 points, and "judges with high percent agreement can still assign vastly different scores". The paper argues for metrics beyond percent agreement, such as Cohen's kappa. Judges show "a tendency toward leniency" and sensitivity to prompt complexity and length. Smaller models and even a lexical metric can still *rank* systems reasonably — [Thakur et al. 2024](https://arxiv.org/abs/2406.12624)
- "Judging the Judges: A Systematic Evaluation of Bias Mitigation Strategies in LLM-as-a-Judge Pipelines" (arXiv 2604.23178, 2026): position bias was negligible (≤0.04) for the modern judges tested. Verbosity direction varies by family: some show a +0.24 to +0.44 preference for longer answers, Claude shows -0.12 (prefers concise) and GPT-4o is about neutral (-0.04). GPT-4o showed a strong cross-model preference (-0.48 toward Claude responses). Best configuration: agreement 0.700, kappa 0.530. Caution: paper versions disagree on the verbosity direction, so check the final version before quoting — [arXiv 2604.23178](https://arxiv.org/html/2604.23178v1)
- "Who Judges Matters: Measuring Family-Conditioned Preference in LLM-as-Judge Panels" (2026) studies judge preference conditioned on model family. The title and topic were found, but the numbers were not extracted — [arXiv 2609.17857](https://arxiv.org/pdf/2609.17857)
- Shankar et al., "Who Validates the Validators?" (UIST 2024) describes **criteria drift**: "users need criteria to grade outputs, but grading outputs helps users define criteria", so evaluation criteria cannot be fully fixed before looking at outputs. EvalGen uses human grades to select the assertions (Python or LLM prompts) that best align with the user, and gets equal or better alignment than SPADE with fewer assertions — [Shankar et al. 2024](https://arxiv.org/pdf/2404.12272)
- Hamel Husain's "critique shadowing" has one domain expert give binary pass/fail with a written critique. The judge prompt is then iterated until it agrees with that expert, and he advises against 1-5 scales — [hamel.dev LLM judge](https://hamel.dev/blog/posts/llm-judge/); summary at [Simon Willison](https://feeds.simonwillison.net/2024/Oct/30/llm-as-a-judge)
- High aggregate agreement can hide weak performance on harder subsets, so check per-slice agreement (secondary guide citing a paper) — [13labs guide](https://www.13labs.au/guides/ai-evals-how-to-know-output-is-right)
- FRAMES scores accuracy with an LLM judge. A third-party note warns the judge "may favor patterns in its own outputs" — [parallel.ai FRAMES explainer](https://parallel.ai/articles/frames-benchmark)

### Inferences
- For Vault, where the generator is OpenAI and the judge is OpenAI flagship: the same-vendor risk is real but its size is unknown. Mitigate it in four ways:
  1. Make judge tasks reference-guided and narrow, for example "does sentence E3.1 support claim X?" rather than "rate quality".
  2. Calibrate on about 40-60 human labels and report TPR/TNR and kappa.
  3. State the risk explicitly in the README.
  4. Optionally spot-check 20 items with a non-OpenAI judge through the dormant Anthropic adapter.
- Pairwise A/B judging should run both orders and count a win only when both orders agree. That costs about 2x on a small sample, which is cheap.
- Report the judge's error rates next to every judged metric. A reviewer can then see that, for example, "faithfulness 0.91 by a judge with 0.88 TPR / 0.93 TNR on 50 labels" is bounded by judge error.

### Gaps
- Did not retrieve kappa values from Thakur et al. (abstract only).
- No quantitative evidence was found on *same-vendor* self-preference for GPT-5/6-family judges specifically.
- Did not find a paper giving a minimum calibration-set size for a judge. The 20-50 figures in practitioner advice are opinion, not evidence.

## A3. How small a golden set can support credible decisions; paired comparisons, bootstrap, CI regression gating

### Takeaway
Treat evals as experiments with error bars. With n=122 binary items, a single system's accuracy has a 95% CI of roughly ±7 percentage points. So only paired comparisons on the same items, using a McNemar or paired bootstrap on per-item differences, can resolve the few-point differences that tuning produces. Criteria also drift, so the golden set should be revised from observed failures, with a frozen test split.

### Cited Findings
- Evan Miller (Anthropic), "Adding Error Bars to Evals" (arXiv 2411.00640, Nov 2024): "Fundamentally, evaluations are experiments." Eval questions should be treated as samples from an unseen super-population. The paper gives formulas for analysing eval data, comparing two models and planning an evaluation, and recommends reporting practices that minimise statistical noise — [Miller 2024](https://arxiv.org/abs/2411.00640). (Only the abstract was retrieved. The specific techniques, such as clustered SEs, paired differences and power analysis, are widely attributed to this paper but were not confirmed here.)
- Criteria drift means the criteria cannot be fully specified before you look at outputs — [Shankar et al. 2024](https://arxiv.org/pdf/2404.12272)
- Practitioner sizing advice, which is opinion: about 20-30 outputs for initial expert labelling ([newsletter](https://highlearningrate.substack.com/p/the-essential-guide-to-llm-apps-evaluation)), and reviewing production traces in batches of 25-50 ([13labs](https://www.13labs.au/guides/ai-evals-how-to-know-output-is-right))
- Ranking systems is easier than per-item scoring. Even small judges and lexical metrics gave reasonable rankings in Thakur et al. — [Thakur et al. 2024](https://arxiv.org/abs/2406.12624)

### Inferences
These are arithmetic, not sourced claims.
- Binomial SE at p=0.8, n=122: sqrt(0.8·0.2/122) ≈ 0.036, so the 95% CI is about ±7.1pp. At p=0.5 it is about ±8.9pp. Per-dataset slices (for example 30-40 items each from CUAD, FinanceBench or QASPER) widen the interval to about ±13-15pp, so do not draw per-slice conclusions from single runs.
- Paired designs help because only discordant items carry signal. If two variants disagree on 10 of 122 items, McNemar needs roughly 9 vs 1 or better to reach p<0.05 (exact binomial on 10 discordant pairs: 9-1 gives p ≈ 0.021 two-sided, and 8-2 gives p ≈ 0.11). Report the discordant counts, not only the two accuracies.
- A paired bootstrap over items (resample item IDs, recompute the metric difference, about 10k resamples) costs no API spend, because it reuses stored per-item scores. It is the cheapest credible "is B better than A" statement.
- Running the same item k times (k=2-3) at nonzero temperature estimates within-item noise. Average per item before bootstrapping, so repeats are not counted as independent items, which is the clustering issue.
- CI gate design is cheap and credible:
  - Every PR runs the deterministic graders on all 122 items using cached retrieval, plus judge graders on a fixed smoke sample of about 20 items with a `--max-usd` cap.
  - Prompt-version bumps run a full-set paired comparison against the last accepted baseline.
  - Fail only on a statistically significant regression, or on any regression in "hard" invariants such as citation validity and refusal-when-unanswerable.
- Keep a frozen held-out test split that is never tuned on. This matches the convergence-engine rule already in the Vault CLAUDE.md.

### Gaps
- Could not confirm promptfoo's or DeepEval's current CI-gating features (for example promptfoo `assert` thresholds or GitHub Action) from primary docs.
- Did not find a published minimum golden-set size backed by power analysis for RAG specifically.

## A4. Public benchmarks that make sensible references for document QA, and typical scores

### Takeaway
Use the public sets Vault already samples (FinanceBench, CUAD, QASPER) as *external validity anchors*, not leaderboard claims. Their published baselines show retrieval is the bottleneck: FinanceBench GPT-4-Turbo scored 19% with a shared vector store vs 79% with long context and 85% with oracle pages. Scores on a 30-40 item subsample are not comparable to full-benchmark leaderboard numbers, and the report should say so.

### Cited Findings
- FinanceBench (Islam et al., Nov 2023, Patronus AI): GPT-4-Turbo with a shared vector store got 19% correct (13% incorrect, 68% failed or refused). Long context got 79% (Claude-2 76%), closed-book about 9% (one source says 11%), and oracle evidence page 85%. One leaderboard row of 19.33% belongs to Llama2 with a shared store, not GPT-4, so check attribution (secondary aggregators) — [FinanceBench paper](https://arxiv.org/abs/2311.11944v1); [Patronus PDF](https://patronus.ai/financebench.pdf); [benchmarklist](https://benchmarklist.com/benchmarks/financebench/)
- Later claims include 94.3% on FinanceBench for DANA (a neurosymbolic method) and KodeX-70B at 79.7% vs GPT-4 at 77.8% under LLM-judge scoring. Both are unverified secondary claims — [emergentmind FinanceBench](https://www.emergentmind.com/topics/financebench-dataset)
- QASPER (Dasigi et al., 2021): existing models underperform humans by at least 27 F1 points when answering from entire papers. With gold evidence, T5-large and UnifiedQA-large reach Answer F1 of 60.03 and 61.39. LED-base reaches 32.80 without gold evidence (another source lists 44.96 or 45.0, and the sources disagree on the setting). The paper reports both Answer F1 and Evidence F1 — [HF paper page](https://huggingface.co/papers/2105.03011.md); [liner review](https://liner.com/review/dataset-informationseeking-questions-and-answers-anchored-in-research-papers); [LED baseline repo](https://github.com/allenai/qasper-led-baseline)
- CUAD (Hendrycks et al., 2021): over 13,000 annotations by dozens of legal experts from The Atticus Project. "Transformer models have nascent performance" — [CUAD](https://arxiv.org/abs/2103.06268)
- FRAMES (Krishna et al., 2024, Google): 824 multi-hop questions, each needing 2-15 Wikipedia articles. Gemini-Pro-1.5 scored 0.40 with no retrieval and 0.66 with a multi-step retrieval pipeline. BM25 with 4 docs scored 47.4% and oracle retrieval 72.9%. Accuracy is graded by an LLM judge — [FRAMES](https://arxiv.org/abs/2409.12941v3); [HF dataset card](https://huggingface.co/datasets/google/frames-benchmark)

### Inferences
- The most defensible external claim for Vault is a per-source breakdown that says, for example, "on N FinanceBench items sampled with seed S, Vault got X% (95% CI), versus the paper's 19% shared-store / 79% long-context GPT-4-Turbo baselines". The baselines are 2023 models and not directly comparable. It shows positioning, not superiority.
- QASPER's Evidence F1 maps naturally onto Vault's citation handles. Report evidence-sentence recall or precision against gold evidence as a retrieval-plus-citation metric. It is deterministic and needs no judge.
- FRAMES is Wikipedia multi-hop, so it does not match Vault's single-corpus document QA. Cite it as a reference only, if at all.

### Gaps
- Did not retrieve CUAD baseline AUPR or precision-at-recall numbers (abstract only). The commonly quoted DeBERTa-xlarge figures were not verified.
- Did not research LongBench (v2) or DocBench scores.
- No reliable 2025-2026 state-of-the-art figure for FinanceBench was found from a primary source.

## B1. Structuring LLM pipelines as explicit typed steps: frameworks vs plain functions

### Takeaway
The best-known practitioner guidance (Anthropic, 12-factor agents, Eugene Yan) converges on a few points:
- Use predefined code paths ("workflows") made of small, typed LLM steps with programmatic gates between them.
- Own prompts and control flow in your code.
- Start from raw APIs rather than frameworks, which "obscure the underlying prompts and responses".
- For a document-QA product with fixed stages, plain typed Python functions plus Pydantic schemas are the evidence-aligned default.

### Cited Findings
- Anthropic, "Building effective agents" (Dec 19, 2024):
  - Frameworks "often create extra layers of abstraction that can obscure the underlying prompts and responses", and "We suggest that developers start by using LLM APIs directly."
  - Workflows are "systems where LLMs and tools are orchestrated through predefined code paths", while agents "dynamically direct their own processes and tool usage."
  - The building block is "an LLM enhanced with augmentations such as retrieval, tools, and memory."
  - Principles: "Maintain simplicity", "Prioritize transparency by explicitly showing the agent's planning steps", and "Carefully craft your agent-computer interface".
  - Prompt chaining allows programmatic "gate" checks on intermediate steps.
  
  Source: [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- 12-Factor Agents (HumanLayer) lists 12 principles, including Factor 2 "Own your prompts" (treat prompts as first-class code, not framework black boxes) and Factor 8 "Own your control flow" — [12-factor agents (fork/mirror)](https://github.com/sahanatvessel/12-factor-agents); primary repo is github.com/humanlayer/12-factor-agents (not fetched); [summary](https://tianpan.co/blog/2026-01-26-12-factor-agents-production-ai)
- Eugene Yan, "Patterns for Building LLM-based Systems & Products" describes seven patterns: evals, RAG, fine-tuning, caching, guardrails, defensive UX and collecting user feedback. They are organised along two axes, improving performance vs reducing cost/risk and data-side vs user-side. Building task-specific evals is the first tenet, and guardrails include syntactic checks such as type, range and JSON validation — [eugeneyan.com](https://eugeneyan.com/writing/llm-patterns/)

### Inferences
- Vault's design rules already match this guidance: the model returns typed decisions and never control flow, routing lives in code, and prompts are versioned files. Cite Anthropic and 12-factor in the README as the rationale.
- A readable structure is one module per stage, each a pure-ish function `(typed input) -> typed output`. There is one gateway for model calls and one OTel span per stage. Pydantic models serve as both the strict JSON schema sent to the model and the internal type.
- Adopt a graph framework (LangGraph-style) only if Vault needs durable cycles or human-in-the-loop pauses that Celery plus `processing_jobs` do not already cover. This is opinion, not evidence.

### Gaps
- Did not research DSPy (signatures, optimisers), LangGraph, Haystack or Pydantic-AI tradeoffs from primary docs. No calls were left. No sourced evidence was gathered on when they "hurt".
- Did not fetch Hamel Husain's or Jason Liu's posts on framework avoidance or on structured outputs (instructor).

## B2. OpenTelemetry GenAI semantic conventions status; Langfuse/Phoenix/OpenLLMetry; per-request cost

### Takeaway
As of Nov 2026 the GenAI semantic conventions are still entirely in "Development" status. They moved out of the core semconv repo in v1.42.0 (June 12, 2026) into `open-telemetry/semantic-conventions-genai`, which has no tagged release. The safe path:
- Use the handful of long-lived attributes (`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`).
- Keep content capture off, which is the default.
- Compute cost yourself from tokens and a price table, since the conventions do not standardise cost.

### Cited Findings
- All GenAI surfaces remain at Development stability: "all 268 stability markers in the repository's model files read `development`". v1.42.0 (June 12, 2026) deprecated and moved all `gen_ai.*` content to the dedicated repo, which had "no releases or tags" as of early Nov 2026 — [John Hodge, Nov 2026 status](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/)
- `gen_ai.provider.name` replaced `gen_ai.system` in v1.37.0 (Aug 2025). `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` replaced the prompt/completion names in v1.27.0 (Aug 2024). `gen_ai.operation.name` and `gen_ai.provider.name` are required on inference, embeddings and token metrics, and `gen_ai.request.model` is required when available — [John Hodge](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/)
- Token metrics were reorganised on Sept 22, 2026. `gen_ai.client.token.usage` and `gen_ai.token.type` were removed and replaced by per-call histograms (for example `gen_ai.client.inference.operation.input_tokens`) and `gen_ai.client.inference.usage.*` counters, and using both double counts. Embedding metrics `gen_ai.client.embeddings.duration` and `...embeddings.operation.input_tokens` were added in Oct 2026 — [John Hodge](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/)
- Retrieval: in-process retrieval gets an INTERNAL `gen_ai.retrieval` span with a `gen_ai.retrieval.duration` metric (Nov 2026). The `gen_ai.retrieval.client` span is deprecated, and hosted vector or search calls should follow DB or client conventions — [John Hodge](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/)
- Content capture: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT` takes NO_CONTENT (the default), SPAN_ONLY, EVENT_ONLY or SPAN_AND_EVENT. The Python instrumentation still reads `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` selects the latest experimental conventions, while the default keeps v1.36-era output — [John Hodge](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/); [python-contrib _semconv.py](https://github.com/open-telemetry/opentelemetry-python-contrib/blob/57083866b564d922498f327143b26812a0c27b5b/opentelemetry-instrumentation/src/opentelemetry/instrumentation/_semconv.py)
- `gen_ai.input.messages` and `gen_ai.output.messages` are opt-in because they can carry personal data. Span names include `chat`, `invoke_agent` and `execute_tool` (secondary blog) — [DEV Community](https://dev.to/azena-ai/opentelemetry-s-genai-semantic-conventions-are-not-stable-yet-heres-what-actually-shipped-in-2026-3mke); [particula.tech](https://particula.tech/blog/opentelemetry-genai-semantic-conventions-stable) (counts 63 gen_ai attribute keys, all Development, as of Aug 13, 2026)

### Inferences
- Vault's rule of no content in spans, only IDs and digests, matches the spec default (NO_CONTENT). Say so explicitly in the README as conformance, not just policy.
- Emit `gen_ai.*` attributes by hand in the gateway (operation, provider, request/response model, input/output tokens) rather than relying on auto-instrumentation, since attribute names are still moving. Pin the semconv version you followed in a constant, and put a computed `vault.cost_usd` (a custom namespace, as GenAI defines no cost attribute) on the span and in a per-request ledger.
- Wrap retrieval in an INTERNAL span named per the new `gen_ai.retrieval` convention. Keep pgvector calls under standard DB client spans.
- Metric labels stay bounded enums (role, stage, outcome), consistent with the existing hard rule.

### Gaps
- Did not verify Langfuse, Arize Phoenix or OpenLLMetry (Traceloop) adoption figures, OTel compatibility status or pricing.
- Did not confirm from the official repo whether any cost attribute proposal exists. The statement that there is none is an inference from its absence in the sources read.
- The primary semconv-genai repository was not fetched directly, so the main source is one well-documented blog post (dated 2026-11-03).

## B3. Explainability for end users: "why this answer", sources, confidence, reason codes

### Takeaway
Showing sources raises trust for factual questions, but trust is poorly calibrated. Random citations also raise trust, checking citations lowers it, and many model-produced citations are not faithful to how the answer was produced. The credible design is *verifiable* explanation: claim-level citations that open the exact stored sentence, deterministic reason codes for abstentions or low coverage, and no model-written self-explanations.

### Cited Findings
- Lajewska & Balog (2026): in a two-stage user study, source attribution had the strongest positive effect on trust for factual and technical questions and was largely ignored for subjective ones. Users' trust judgments do not always align with objective response quality — [arXiv 2601.14460](https://arxiv.org/abs/2601.14460v1)
- "Citations and Trust in LLM Generated Responses" (2025): trust rose when citations were present *even when citations were random*, and fell when participants checked them — [arXiv 2501.01303](https://fugumt.com/fugumt/paper_check/2501.01303v1_enmode) (mirror; original is arxiv.org/abs/2501.01303)
- Wallat et al. (2025), "Correctness is not Faithfulness in RAG Attributions": up to 57% of citations lack faithfulness, meaning they are correct-looking but not what the answer was based on, which risks misplaced trust — [Wallat et al.](https://staff.fnwi.uva.nl/m.derijke/wp-content/papercite-data/pdf/wallat-2025-correctness.pdf); [TU Delft record](https://repository.tudelft.nl/record/uuid:bab4a12e-455b-49ea-bdf8-7f6a88478f60)
- PaperTrail (2026) argues that source-level citations are not granular enough for rigorous verification, and compares claim-evidence matching against a citation baseline in a study with 26 researchers — [arXiv 2602.21045](https://arxiv.org/pdf/2602.21045v1)
- Anthropic recommends "Prioritiz[ing] transparency by explicitly showing the agent's planning steps" — [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- Eugene Yan lists "defensive UX" as a core pattern — [eugeneyan.com](https://eugeneyan.com/writing/llm-patterns/)

### Inferences
- Vault's handle-based citations, where code copies `cited_text` from stored sentences, address the Wallat and "random citations" problem better than model-written quotes. This is a strong, reviewer-visible differentiator, and it should be demonstrated with an eval metric: the share of answer claims whose cited sentence a judge or human confirms supports them.
- A "why this answer" panel built from reason codes and templates could show: retrieved passages ranked with scores, which were cited, coverage, and abstention or partial-answer codes such as `INSUFFICIENT_EVIDENCE` and `CITATION_UNRESOLVED`. This follows from the evidence, since model self-explanations are not verifiable.
- Be careful with numeric "confidence" displays. No evidence was found that they help, and the random-citation result shows that trust signals can mislead.

### Gaps
- No study was found that directly evaluates "why this answer" panels or confidence displays in RAG products. A vendor blog mentions an ACM study on confidence displays that could not be located.
- Did not survey product examples such as Perplexity, NotebookLM or Glean citation UIs from primary sources.

## B4. Prompt management: versioned prompts, registries, structured outputs, eval-gated changes

### Takeaway
Practitioner consensus, which is opinion-backed, is to own prompts as code: keep them in version control, review them, and gate changes on evals. Structured outputs (strict JSON schema) turn model outputs into typed decisions that code validates. Vault's existing `prompts/<id>@<version>.md` plus a mandatory eval run on version bumps is aligned with this. A hosted prompt registry adds little for a small team.

### Cited Findings
- 12-Factor Agents Factor 2, "Own your prompts": treat prompts as first-class code, not framework black boxes — [12-factor agents](https://github.com/sahanatvessel/12-factor-agents); [summary](https://tianpan.co/blog/2026-01-26-12-factor-agents-production-ai)
- Criteria drift implies eval criteria co-evolve with prompts, so the golden set and grader prompts also need versioning — [Shankar et al. 2024](https://arxiv.org/pdf/2404.12272)
- "When Generic Prompt Improvements Hurt: Evaluation-Driven Iteration for LLM Applications" (2026) is an evaluation-driven iteration paper whose title indicates generic prompt "improvements" can regress task metrics. The contents were not read — [arXiv 2601.22025](https://arxiv.org/pdf/2601.22025)
- OpenAI `label_model` graders require models that support structured outputs — [Azure OpenAI graders](https://learn.microsoft.com/en-au/Azure/foundry/concepts/evaluation-evaluators/azure-openai-graders)
- Guardrails include syntactic type, range and JSON validation of outputs — [eugeneyan.com](https://eugeneyan.com/writing/llm-patterns/)

### Inferences
- Record `prompt_id@version`, its content digest, the model, the reasoning effort and the schema version on every span and eval-result row. Then any metric can be traced to an exact prompt, which is cheap reproducibility.
- Grader and judge prompts are prompts too. Version them, and re-run judge calibration when they change.
- Even under strict schema, code must validate semantics: handles exist, enums are in range, spans are within the doc. Strict schema guarantees shape, not truth.

### Gaps
- OpenAI's "Introducing Structured Outputs" page returned HTTP 403. The often-quoted reliability figure (strict mode reaching 100% schema adherence on OpenAI's complex-schema eval vs under 40% for gpt-4-0613) was **not verified** in this session and should not be quoted without re-checking. Schema-feature limitations and first-request schema-processing latency were also not verified.
- Did not review Langfuse, PromptLayer or Braintrust prompt-registry docs, or promptfoo CI integration, from primary sources.
