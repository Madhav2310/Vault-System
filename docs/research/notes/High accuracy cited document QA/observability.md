# Observability and continuous evaluation for production LLM/RAG systems (2024-2026)

Context: Vault already has OTel spans, a run ledger (per-node steps, timings, costs), reason codes plus template explanations, and an offline eval harness with paired bootstrap and McNemar tests. It has no online eval, no per-stage error attribution in reports, and no CI quality gate. Research date: 2026-11-14.

## 1. OpenTelemetry GenAI semantic conventions: status, attributes, tool support

### Takeaway
The GenAI conventions are still **Development** status, not stable. They now live in a separate repo, `open-telemetry/semantic-conventions-genai`. They define a `gen_ai.retrieval` span and a `gen_ai.evaluation.result` event, so Vault can emit spec-shaped retrieval and online-eval signals today without vendor lock-in. Expect attribute churn.

### Cited Findings
- The old opentelemetry.io GenAI pages now only say the conventions moved: "This page has moved and is no longer maintained in this repository", pointing to github.com/open-telemetry/semantic-conventions-genai. — [OTel docs](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- The new repo's README covers "spans, metrics, and events for GenAI clients", plus MCP and provider-specific conventions. Its schema URL is listed as "TODO", and its CHANGELOG has no released entries yet (towncrier, "Unreleased"). — [semantic-conventions-genai repo](https://github.com/open-telemetry/semantic-conventions-genai)
- `docs/gen-ai/README.md` says "**Status**: Development". It lists these signal docs: Client Inference, Client Embeddings, Execute Tool, Retrieval (spans and metrics), Client Agent Invocations, Events, Exceptions, Metrics, Inference Token Metrics, Model spans, Realtime and Agent spans. It also has provider docs for Anthropic, Azure AI Inference, AWS Bedrock and OpenAI. The shared context attribute is `gen_ai.conversation.id`. — [repo docs/gen-ai/README.md](https://github.com/open-telemetry/semantic-conventions-genai/tree/main/docs/gen-ai)
- **Retrieval span:** `gen_ai.retrieval` is "an execution of a framework retrieval pipeline that returns documents relevant to a query". Span name is `{gen_ai.operation.name}`, kind INTERNAL, and `gen_ai.operation.name` = `retrieval` (Required).
  - `gen_ai.retrieval.top_k` (Recommended, int).
  - `gen_ai.retrieval.documents` (Opt-In). Each item is `{id, score}` per a JSON schema.
  - `gen_ai.retrieval.query.text` (Opt-In).
  - `error.type` (Conditionally Required).
  - Metric: `gen_ai.retrieval.duration` histogram (s).
  — [repo docs/gen-ai/retrieval.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/retrieval.md)
- **Inference span:** name SHOULD be `{gen_ai.operation.name} {gen_ai.request.model}`.
  - `gen_ai.provider.name` (Required, for example `openai`).
  - `gen_ai.request.model` (Conditionally Required).
  - `gen_ai.response.finish_reasons`.
  - Token usage: `gen_ai.usage.input_tokens` / `output_tokens` (Recommended), plus `gen_ai.usage.cache_read.input_tokens`, `cache_write.input_tokens` and `reasoning.output_tokens` ("When available and non-zero").
  - Content is **Opt-In**: `gen_ai.input.messages`, or `gen_ai.input.messages_ref` ("An absolute URI identifying the input messages in external storage", for example an s3:// URI).
  — [repo docs/gen-ai/client-inference.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md)
- **Token metrics** include `gen_ai.client.inference.usage.input_tokens`, `.output_tokens`, `.cache_read.input_tokens`, `.cache_write.input_tokens` and `.reasoning.output_tokens`. — [same file](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md)
- **Other metrics:**
  - `gen_ai.client.operation.duration`, `gen_ai.server.request.duration`, `gen_ai.server.time_to_first_token`, `gen_ai.server.time_per_output_token`.
  - Agent metrics: `gen_ai.invoke_agent.duration`, `gen_ai.invoke_agent.inference_calls`, `gen_ai.invoke_agent.tool_calls`.
  — [repo docs/gen-ai/gen-ai-metrics.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
- **Evaluation event `gen_ai.evaluation.result`** (Development): "captures the result of evaluating GenAI output for quality, accuracy, or other characteristics. This event SHOULD be parented to GenAI operation span being evaluated when possible or set `gen_ai.response.id` when span id is not available." Attributes:
  - `gen_ai.evaluation.name` (Required, for example `Relevance`).
  - `gen_ai.evaluation.score.label` (Cond. Required, for example `pass`/`fail`/`relevant`). The spec says it "SHOULD have low cardinality".
  - `gen_ai.evaluation.score.value` (double).
  - `gen_ai.evaluation.explanation` (Recommended, free text).
  - `gen_ai.response.id`.
  - `error.type`.
  — [repo docs/gen-ai/gen-ai-events.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md)
- OTel records evaluation results but does not perform evaluation. The evaluator can be application logic, a rule, a model judge, or another system. — [TrueFoundry blog (vendor, secondary)](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions)
- When an evaluator call fails, Dash0 still emits the event with an `error.type` and no score. — [Dash0 docs (vendor)](https://www.dash0.com/docs/dash0/ai/observability/evals/eval-result-records)
- **Arize Phoenix** uses OpenInference as its native convention. Since arize-phoenix **15.10.0** (release notes dated 2026-05-15) it converts `gen_ai.*` to OpenInference at ingest:
  - `gen_ai.input.messages` → `llm.input_messages.*`
  - retrieval documents → `retrieval.documents.*`
  - `gen_ai.usage.*` → `llm.token_count.*`
  - When OpenInference attributes are already present, "those values take precedence" and "conversion only fills in what's missing".
  — [Phoenix release notes](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
- Datadog LLM Observability accepts OTel GenAI semconv (search snippet says 1.37+) or OpenInference. — [Datadog docs](https://docs.datadoghq.com/llm_observability/instrument/otel_instrumentation/) (version number seen only in a search snippet; not verified on the page)
- Langfuse exposes an OTLP HTTP endpoint at `/api/public/otel` that maps `gen_ai.*` into its trace model; gRPC is not supported. — [third-party write-up](https://medium.com/@sharanharsoor/opentelemetry-for-llmops-how-langfuse-achieved-universal-multi-language-support-without-building-782d843adf3c) (secondary; not confirmed on langfuse.com)
- A dev.to post says the GenAI conventions were deprecated in the main repo and moved on 2026-06-12. — [dev.to (secondary)](https://dev.to/azena-ai/opentelemetrys-genai-semantic-conventions-are-not-stable-yet-heres-what-actually-shipped-in-2026-3mke) The move itself is VERIFIED via the OTel docs page above. The exact date is CLAIMED.

### Inferences
- Vault can map its existing spans directly to the spec:
  - retrieval node → `gen_ai.retrieval` span with `top_k`, and `documents` as `{id: chunk_id, score}`.
  - LLM gateway → inference span with `gen_ai.provider.name`, `gen_ai.request.model` and the usage/cache/reasoning tokens.
  - Each online judge verdict or citation validator outcome → a `gen_ai.evaluation.result` event parented to the answer span.
- Vault's hard rule is "no document text, prompts, or completions in logs or spans". That matches the spec's Opt-In status for content attributes. Leave `gen_ai.input.messages` and `retrieval.query.text` off. `*_ref` URIs pointing to owner-scoped storage are the spec-sanctioned escape hatch.
- `gen_ai.evaluation.explanation` is free text, which conflicts with Vault's rule that "explanations come from reason codes plus templates". Put the reason code in `score.label` (low-cardinality enum, as the spec asks). Either omit `explanation` or fill it from the template.
- Because the spec is in Development, pin the semconv version and keep the mapping in one module (for example `app/observability/genai_semconv.py`) so renames cost one file.

### Gaps
- I could not read the repo's `reference/README.md` compliance matrix. So I could not verify which vendors implement which attributes: OpenLLMetry/Traceloop, Braintrust, LangSmith and W&B Weave are all unverified.
- I could not confirm the current `OTEL_SEMCONV_STABILITY_OPT_IN` values (for example `gen_ai_latest_experimental`) for the new repo. Grepping client-inference.md found no matches.

## 2. Per-stage error attribution for RAG and error-analysis workflow

### Takeaway
The credible way to attribute a RAG failure to a stage is claim-level entailment between three sets: ground-truth claims, retrieved chunks and response claims (RAGChecker). A cascade that blames the earliest faulty stage is the alternative (RAGEC, EACL 2026). Automated stage attribution agrees with humans only about 58% of the time, and error-type labels about 40%. Treat automated attribution as triage that needs human-coded error analysis (open coding then axial coding, about 100 traces) on top.

### Cited Findings
- **RAGChecker** (Amazon, arXiv 2408.08067) uses "claim-level entailment". Extractor and checker LLMs are configurable, and the only required annotation per query is `gt_answer`. — [RAGChecker repo](https://github.com/amazon-science/RAGChecker)
- **RAGChecker metrics** (k = number of retrieved chunks):
  - Claim recall = GT claims entailed in retrieved chunks / all GT claims.
  - Context precision = relevant chunks (containing at least one GT claim) / k.
  - Context utilization = GT claims entailed in chunks *and* response / GT claims entailed in chunks.
  - Relevant noise sensitivity = incorrect response claims entailed in a relevant chunk / all response claims.
  - Irrelevant noise sensitivity = incorrect response claims entailed only in irrelevant chunks / all response claims.
  - Hallucination = incorrect response claims not entailed in any chunk / all response claims.
  - Self-knowledge = correct response claims not entailed in any chunk / all response claims.
  - Faithfulness = response claims entailed in retrieved chunks / all response claims.
  — [RAGChecker paper](https://arxiv.org/html/2408.08067v2)
- **RAGChecker meta-evaluation:** 280 pairwise instances, 10 domains, two annotators. Overall Pearson/Spearman: RAGChecker 61.93/60.90, RAGAS answer similarity 48.31/57.23, TruLens answer relevance 35.15/33.59, ARES answer relevance 17.81/16.26. The human inter-annotator ceiling was 70.09/68.89. — [RAGChecker paper](https://arxiv.org/html/2408.08067v2)
- **RAGChecker findings:**
  - "Retriever Matters Consistently."
  - Context utilization correlates strongly with overall F1.
  - Higher claim recall makes generators more noise-sensitive.
  - Relevant noise sensitivity is consistently higher than irrelevant noise sensitivity ("relevant chunks are trusted as a whole").
  - Explicit prompt requirements raised faithfulness but also noise sensitivity.
  — [RAGChecker paper](https://arxiv.org/html/2408.08067v2)
- **RAGEC taxonomy** (Leung, Cresswell et al., arXiv 2510.13975, EACL 2026), 16 types by stage:
  - Chunking: E1 overchunking, E2 underchunking, E3 context mismatch.
  - Retrieval: E4 missed retrieval, E5 low relevance, E6 semantic drift.
  - Reranking: E7 low recall, E8 low precision.
  - Generation: E9 abstention failure, E10 fabricated content, E11 parametric overreliance, E12 incomplete answer, E13 misinterpretation, E14 contextual misalignment, E15 chronological inconsistency, E16 numerical error.
  — [arXiv 2510.13975](https://arxiv.org/html/2510.13975v2); code at github.com/layer6ai-labs/rag-error-classification
- **RAGEC auto-classifier accuracy against humans:**
  - Correct/incorrect answer judge: 92.9% agreement.
  - Stage attribution: 57.8%. It is a rules-based cascade over generation, reranking, then chunking vs retrieval, and it picks the earliest faulty stage. Humans blame retrieval more often; RAGEC blames the generator more often.
  - Error type: 40.3% (LLM judge labels 10 times, modal label taken).
  — [arXiv 2510.13975](https://arxiv.org/html/2510.13975v2)
- **RAGEC error distribution** (DragonBall-EN, 377 confirmed errors): generation 32.7%, retrieval 31.5%, chunking 29.7%, reranking 7.1%. Fabricated content (E10) "is relatively rare in our analysis". Medical queries were dominated by chunking errors (E3). — [arXiv 2510.13975](https://arxiv.org/html/2510.13975v2)
- **Earlier taxonomies:** Barnett et al. 2024, "Seven Failure Points When Engineering a RAG System". Garani (TrustNLP 2026 workshop) proposes 33 failure modes across 7 stages; I saw only its abstract. — [search results / ACL Anthology](https://aclanthology.org/people/anupama-garani/unverified/)
- **RAG triad (TruLens):**
  - Context relevance localizes retrieval failures.
  - Groundedness localizes generation failures. The response is split into claims, each checked for support in the context.
  - Answer relevance catches answers that are on-context but off-question.
  — [TruLens docs](https://www.trulens.org/getting_started/core_concepts/rag_triad/)
- **ARES** (NAACL 2024) fine-tunes lightweight judges for context relevance, answer faithfulness and answer relevance. It uses prediction-powered inference with "a few hundred" human annotations to correct judge error. — [arXiv 2311.09476](https://arxiv.org/abs/2311.09476)
- **Hamel Husain on error analysis:**
  - He calls it "the most important activity in evals". Steps: representative traces → open coding (free-text notes) → axial coding (group into a failure taxonomy and count) → iterate.
  - Review at least 100 traces, stopping at "theoretical saturation". Annotate 30+ yourself before letting an agent propose failures.
  - Use binary pass/fail rather than Likert.
  - Build one judge per failure mode, and validate it with TPR/TNR on a train 10-20% / dev 40-45% / test 40-45% split. Label "100 to 200 examples for each failure mode".
  - The authors spend "60-80% of our development time on error analysis and evaluation."
  - For RAG: "debug retrieval first using IR metrics, then tackle generation quality using properly validated LLM judges."
  — [Hamel Husain, Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)
- **Eugene Yan, judge alignment:**
  - Use binary labels and aim for "at least 50-100 failures out of 200+ total samples", with a 75/25 split for alignment and test.
  - Measure precision, recall and Cohen's kappa, prioritizing recall on the fail class. He writes that kappa "0.4 - 0.6 indicates substantial agreement while anything above 0.7 is excellent", and that human annotators are often around 0.2-0.3.
  - For pairwise judging, swap the order to counter position bias.
  — [Eugene Yan, Product Evals in Three Simple Steps](https://eugeneyan.com/writing/product-evals/)

### Inferences
- Vault has stored sentence handles and application-managed citations, so it can compute RAGChecker-style attribution with less LLM cost than usual. Retrieval recall and precision come from gold evidence spans against retrieved chunk IDs, which is deterministic. Rerank loss is gold present in the pre-rerank candidates but missing after the cutoff, also deterministic. Citation failures come from `CITATION_UNRESOLVED` and cited-handle-not-in-gold, again deterministic. Only generation claim entailment needs a judge.
- A deterministic "earliest faulty stage" cascade is a credible design for a per-stage failure report: ingestion/chunking → retrieval → rerank → generation → citation → grading. It mirrors RAGEC but replaces RAGEC's weakest part (the LLM stage call, 57.8% human agreement) with code over Vault's ledger. The cascade emits a reason code per failed item, which fits the reason-code and template architecture.
- Add "grading" as its own stage, meaning judge disagreement with gold. Judge TPR/TNR on a held-out human-labeled set should be reported alongside answer metrics.

### Gaps
- I found no published production dashboards or numbers from a named company for stage-level RAG failure shares, only RAGEC's benchmark distribution.
- I did not find accuracy figures for RAGChecker's stage attribution itself; its correlations are for answer quality.

## 3. CI regression gates for LLM quality

### Takeaway
Primary guidance (Anthropic's statistics paper, Hamel Husain, Eugene Yan) converges on the same gate design. Gate on **paired differences with confidence intervals and power analysis**, not on raw thresholds. Keep CI sets small and mostly deterministic (100+ items), with expensive judges used sparingly. Feed production failures back into the CI set. Concrete published sample-size rules for CI gates are rare. The practitioner posts that exist are blog-grade.

### Cited Findings
- **Anthropic, "A statistical approach to model evals" (Nov 2024)**, five recommendations:
  1. Report SEM, with CI = mean ± 1.96 × SEM.
  2. Cluster standard errors on the unit of randomization. Clustered SEs "can be over three times as large as naive standard errors".
  3. Reduce within-question variance by resampling answers several times, or by using token probabilities.
  4. Analyze paired differences, "a 'free' variance reduction technique". Question-score correlations between frontier models are about 0.3-0.7.
  5. Use power analysis to choose the number of questions for a target effect (for example, 3 percentage points).
  — [Anthropic research](https://www.anthropic.com/research/statistical-approach-to-model-evals)
- **Hamel Husain on CI vs production:**
  - "CI evals protect against known regressions before deployment."
  - CI sets are small, "in many cases 100+ examples", and should favor "assertions or other deterministic checks over LLM-as-judge evaluators".
  - Add representative production failures to CI so "This mitigates regressions on new issues".
  - "If the eval passes all examples, its giving you no new information." Retire or harden saturated evals.
  — [Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)
- **Eugene Yan, sample-size example:** with a 5% defect requirement, 200 samples showing 3% defects gives ±2.4%, which is inconclusive. 400 samples give ±1.7%, so the upper bound of 4.7% passes. He also says to run the eval harness on every config change (prompt, retrieval, model). — [Product Evals](https://eugeneyan.com/writing/product-evals/)
- **Practitioner gate pattern** (blog, 2026): pin inputs and judge version. Separate deterministic and stochastic layers. Add "critical-slice invariants". Use a paired bootstrap CI with a non-inferiority margin, blocking when the lower bound is at or below the tolerated regression. Fail visibly on evaluator errors. — [OneUptime blog](https://oneuptime.com/blog/post/2026-08-31-llm-evaluation-ci-regression-gate/view) (CLAIMED, vendor blog)
- Measure output, judge and dataset variance before setting thresholds. The same post cites "up to 10%" accuracy fluctuation across repeated identical runs under deterministic configs. — [OneUptime blog](https://oneuptime.com/blog/post/2026-08-31-measure-llm-eval-variance-thresholds/view) (the 10% figure is attributed to a 2025 study I did not verify)
- Multi-run assertions and sequential testing (SPRT) to cut cost. Do not "fix" flakiness by loosening thresholds, because engineers learn to retry until the gate passes. — [tianpan.co blog](https://tianpan.co/blog/2026-04-10-non-deterministic-ci-agentic-systems) (CLAIMED, individual blog)

### Inferences
- Vault already has paired bootstrap and McNemar tests, which is the hard part. A credible gate:
  - **Tier 0**, every PR, no LLM cost: deterministic checks over replayed or cached model outputs (citation resolution rate, schema validity, retrieval recall@k against gold), all on the retrieval-only and replay paths.
  - **Tier 1**, on prompt, model or routing changes: a fixed smoke split of about 100-200 items with a `--max-usd` cap. Paired comparison against the stored baseline run, with non-inferiority margins per metric, and McNemar on per-item pass/fail.
  - **Must-not-regress slices** (for example unanswerable → abstain, citation validity) gated separately from the mean.
  - The baseline is a pinned ledger run ID, not a re-run, which avoids double noise.
- Power analysis tells Vault honestly what the gate can detect. With about 150 paired items and per-item pass rates near 80-90%, only fairly large regressions (several points) are detectable. State the minimum detectable effect in the gate output rather than implying sensitivity it doesn't have. (This is an inference; compute it from Vault's own discordant-pair rates.)
- Cluster SEs by document when several questions share a document, per Anthropic's point 2.

### Gaps
- I found no first-party engineering blog (OpenAI, Databricks, Honeycomb) giving concrete CI gate sample sizes or flake rates. Searches surfaced mainly vendor or individual blogs.

## 4. Online evaluation: sampling, cheap judges and guardrails, feedback loops, drift, cost

### Takeaway
The standard pattern:
- Cheap deterministic checks on 100% of traffic.
- Asynchronous LLM judges on a filtered sample (commonly about 10%, or a nightly batch). Vendor-estimated cost is about $0.01-0.10 per judged item.
- Synchronous guardrails only for fast, clear-cut failures.
- Production failures promoted into the offline and CI datasets.
- Confidence intervals tracked on online metrics.

### Cited Findings
- **Hamel Husain on online evaluation:**
  - Guardrails are inline, "Fast and deterministic", millisecond latency. Evaluators run after the response and "do not block the original answer". "You would almost never use a slow or non-deterministic LLM-as-Judge as a synchronous guardrail" (a judge cascade on borderline cases is the exception).
  - Pattern: "run cheap checks on every trace and an expensive judge on a nightly sample". Track confidence intervals: "If the lower bound crosses your threshold, investigate further."
  - Production lacks references, so it relies more on reference-free judges.
  — [Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)
- **Hamel Husain on sampling production traces for review:**
  - Methods: random, clustering, data analysis (outliers on latency or tool count), evaluator classification, user feedback. "Keep some random traces in every batch."
  - Cadence: at least 100 fresh traces per cycle every 2-4 weeks, plus 10-20 traces weekly focused on outliers.
  — [Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)
- **Langfuse online LLM-as-judge:**
  - Rules filter observations by type, name, metadata, userId, sessionId, tags or version, then apply a sampling rate. There is an initial evaluation delay.
  - The worker calls the judge and writes "one score per observation per evaluator".
  - Cost estimate "$0.01-0.10 per assessment".
  - Self-host setting `LANGFUSE_LLM_AS_JUDGE_EXECUTION_WORKER_CONCURRENCY` (default 5).
  — [Langfuse docs](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge) (the cost figure is a vendor estimate)
- **LangSmith online evaluators:** a filter is applied first, then the sampling rate (UI 0-100%, API `sampling_rate` 0-1). The docs suggest, for example, applying the evaluator to 10% of traces to control cost, and rules can be backfilled to past runs. — [LangSmith rules docs](https://docs.langchain.com/langsmith/rules); [LangSmith online evals](https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge)
- **ARES** uses prediction-powered inference: a few hundred human labels statistically correct a cheap judge's bias. This is relevant for turning sampled online judge scores into unbiased production estimates. — [arXiv 2311.09476](https://arxiv.org/abs/2311.09476)

### Inferences
- Vault already computes several "free" online signals per request:
  - citation-resolution rate and `CITATION_UNRESOLVED` count
  - abstain/refusal rate by reason code
  - retrieval score distributions and top-1 margin
  - rerank-degraded flag
  - cost and latency per node from the ledger
  
  Emitting these as `gen_ai.evaluation.result` events or as bounded-label metrics gives 100% coverage at near-zero cost, consistent with "cheap checks on every trace".
- An async Celery task can judge a sampled 5-10% of answers, using groundedness of each cited sentence against its handle text with the `fast` role. It should be idempotent, keyed on run_id plus evaluator version, write to a judge-results table, and stay outside the request path. Also cap spend per day with a budget. At the $0.01-0.10 vendor range, 1,000 answers/day × 10% = $1-10/day. That number is illustrative, not measured.
- Feedback loop: user thumbs and flags plus judge fails → error-analysis queue → promote labeled items into a `prod_regressions` split of the eval set. This is the CI-dataset feedback Hamel describes. Keep this split out of tuning data per Vault's test-split rule.
- Drift: use the existing ledger to chart a weekly moving window of retrieval score distributions, abstain rate and judge-fail rate with CIs, and alert when the CI lower bound crosses a threshold.

### Gaps
- I found no authoritative source with measured production numbers for judge cost per 1k requests or for drift-detection methods specific to RAG (for example, embedding-distribution drift tests). The cost figures above are vendor estimates.
- I did not verify Braintrust, Datadog or W&B Weave online-eval sampling specifics.

## 5. Minimal but credible setup for a small team or take-home, without vendor lock-in

### Takeaway
Use OTel as the only instrumentation API, with GenAI semconv attribute names. Send traces to a self-hostable OTLP backend: grafana/otel-lgtm, which Vault already runs, or Phoenix or Langfuse, which both ingest `gen_ai.*`. Keep evaluation logic in-repo: deterministic stage attribution, a paired-statistics CI gate, sampled async judges emitting `gen_ai.evaluation.result`, and a human error-analysis loop. Any vendor then becomes a swappable sink.

### Cited Findings
- Phoenix ingests OTel GenAI traces natively since v15.10.0, with no code changes. — [Phoenix release notes](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
- Langfuse offers an OTLP HTTP ingest endpoint and self-hosted online judges. — [Langfuse docs](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge); [secondary on OTLP endpoint](https://medium.com/@sharanharsoor/opentelemetry-for-llmops-how-langfuse-achieved-universal-multi-language-support-without-building-782d843adf3c)
- RAGChecker (Apache-licensed Amazon repo, license not verified) and the RAGEC code are open source and usable offline for diagnostics. — [RAGChecker](https://github.com/amazon-science/RAGChecker); [RAGEC paper](https://arxiv.org/html/2510.13975v2)
- Advice to spend most effort on error analysis rather than tooling: "60-80% of our development time on error analysis and evaluation". — [Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)

### Inferences (proposed Vault upgrade, ranked by value per effort)
1. **Per-stage failure report** in the existing eval harness. A deterministic earliest-faulty-stage cascade over the ledger: chunk/ingest, retrieval, rerank, generation, citation, grading. Report counts and CIs per stage and per reason code, and diff them between paired runs. No new service, and it addresses the biggest gap.
2. **CI quality gate:**
   - Tier 0 on every PR: replay or retrieval-only, deterministic, $0.
   - Tier 1 on a prompt/model label: about 150-item smoke split, `--max-usd` capped. Paired bootstrap plus McNemar against a pinned baseline run, with non-inferiority margins, separately gated invariants, and the MDE printed.
3. **Spec-aligned telemetry:** rename and add attributes to GenAI semconv (`gen_ai.retrieval` span, usage/cache/reasoning tokens, `gen_ai.evaluation.result` events with reason-code labels). Keep content Opt-In off.
4. **Online eval:** free signals on 100% of traffic. A sampled async `fast`-role groundedness judge with a daily USD cap. Judge TPR/TNR validated on 100-200 human labels before trusting it. Weekly review of about 20 outlier traces, which feeds a prod-regression split.
5. Optional: point the OTLP exporter at Phoenix or Langfuse for a trace UI. This is not required, since otel-lgtm already exists.
- Hostile-reader note: any "online accuracy" number from a sampled LLM judge must ship with the judge's measured agreement against human labels and a CI. Otherwise it is an unvalidated self-authored metric.

### Gaps
- I found no published reference architecture of this exact "OTel plus in-repo evals" minimal stack from a first-party engineering blog. The recommendation above is a synthesis.
- Vendor feature claims for Braintrust, LangSmith OTel ingestion, W&B Weave and OpenLLMetry attribute coverage were not checked.
