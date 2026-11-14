# Answer generation and latency strategies for cited document QA (2024-2026)

Context: Vault pipeline is query analysis (~2 s, small model), hybrid retrieval (~0.6 s), generation (~2.2 s, mid-tier OpenAI reasoning model, low effort, structured JSON segments with evidence handles), support check (~1.6 s). TTFT ~3.3 s, total ~6 s. 5/44 eval items judged incomplete, mostly compare questions that miss one side.

Confidence tags: VERIFIED = read in a primary source during this session; CLAIMED = vendor or secondary claim, or primary source not re-fetched this session; UNKNOWN = not found.

## Prompting for complete and faithful answers (quote-first, schema design, required elements, multi-part, few-shot, structured outputs, reasoning effort, model size)

### Takeaway
The best-evidenced lever is a reason-before-answer order inside one structured call: the model extracts or selects evidence first (quote/handle selection), then writes the answer, with schema fields ordered reasoning/evidence → answer. The "structured output hurts reasoning" result is mostly about answer-before-reason ordering and prompt mismatch, not about JSON as such. Evidence on reasoning effort is coarse: low effort is close to medium on aggregate intelligence indexes, minimal is much worse.

### Cited Findings
- VERIFIED. Anthropic long-context guidance: "Put long-form data at the top ... above your query, instructions, and examples. This improves performance across all models." It also says to wrap each document in `<document>` tags with `<document_content>` and `<source>` subtags, and to "Ground responses in quotes: For long document tasks, ask Claude to quote relevant parts of the documents first before carrying out its task. This helps Claude focus on the relevant content and ignore the rest of the document." The current page gives no percentage gain. (An earlier version of this page claimed queries at the end improved quality "by up to 30%"; that figure was not on the page fetched this session, so treat it as CLAIMED.) — [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/long-context-tips)
- VERIFIED. "Let Me Speak Freely?" (Tam et al., EMNLP 2024 Industry), zero-shot accuracy, text vs JSON format-restricting instruction (FRI) vs JSON-mode: GPT-3.5-Turbo GSM8K 76.6 (text) / 49.3 (JSON FRI) / 29.87 (JSON-mode); Last Letter 56.7 / 25.2 / 1.78. Claude 3 Haiku GSM8K 86.5 text vs 23.4 JSON FRI. Gemini 1.5 Flash nearly unaffected (GSM8K 89.3 vs 89.2). LLaMA-3-8B GSM8K 74.7 vs 48.9. — [arXiv 2408.02442v3](https://arxiv.org/html/2408.02442v3)
- VERIFIED. Root cause the paper itself reports: "100% of GPT 3.5 Turbo JSON-mode responses placed the 'answer' key before the 'reason' key", so the model answered zero-shot with no chain of thought. A two-step NL-to-Format pipeline (answer in prose, then convert) performed "nearly identical" to free text. The authors tested only simple two-field schemas. — [arXiv 2408.02442v3](https://arxiv.org/html/2408.02442v3)
- VERIFIED. Rebuttal (dottxt, Nov 2024): with the same prompt for both conditions, a one-shot example of the target structure, and a schema with `reasoning` before `answer`, Llama-3-8B-Instruct scored: GSM8K 0.77 unstructured vs 0.78 structured; Last Letter 0.73 vs 0.77; Shuffled Objects 0.41 vs 0.44. They also show the original paper's answer parser changed scores a lot (Last Letter: strict regex 0.35, AI parser 0.57, flexible regex 0.61). This is a vendor of structured-generation tooling (CLAIMED interest). — [dottxt "Say What You Mean"](https://blog.dottxt.ai/say-what-you-mean.html)
- CLAIMED (search summary, not fetched). An independent replication found structured as good or better for LLaMA3-8B, but "did not hold for GPT-4o-mini (and possibly other models)". — [Dylan Castillo](https://dylancastillo.co/posts/say-what-you-mean-sometimes.html)
- VERIFIED (independent benchmark). Artificial Analysis, GPT-5 by reasoning effort (Aug 2025): intelligence index Medium 67, Low 64, Minimal 44 (≈ GPT-4.1). Token use roughly 23x between High and Minimal. — [Artificial Analysis](https://artificialanalysis.ai/articles/gpt-5-benchmarks-and-analysis)
- CLAIMED (vendor). OpenAI's GPT-5 prompting guide says many workflows "can be accomplished with consistent results at medium or even low reasoning_effort". — [OpenAI GPT-5 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide)
- CLAIMED (anecdote). A developer reported that at low effort "the agent misses a critical step / tool call", and that latency savings on simple questions were "not ... very impactful". — [OpenAI community thread](https://community.openai.com/t/gpt-5-reasoning-effort-impact-on-agent-performance/1359021)
- VERIFIED. OpenAI latency guide: ask for "only the structured fields you need", lower `reasoning.effort` for simple tasks, and note that `max_output_tokens` also caps reasoning tokens, so a low cap can truncate a response. — [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)

### Inferences
- In Vault's JSON schema, put a `plan`/`required_elements` field (one entry per sub-question or entity, each listing the evidence handles that cover it) before the answer `segments`. Code can then check that every planned element has at least one valid handle. This is a quote-first, reason-before-answer design that keeps one call, and it turns "missing one side" into a deterministic check rather than a judge finding.
- The format penalty in Tam et al. comes mainly from answer-before-reason ordering. Vault already uses handles instead of quotes, so the risk is less about JSON itself and more about the schema letting the model skip planning. Do not split generation into prose + convert (extra latency, ~1 call) unless an eval shows the structured call loses accuracy.
- Low effort is a reasonable default for single-fact questions. For compare/multi-part questions (route chosen by query analysis, in code), raising effort to medium is the cheapest test of whether incompleteness is a reasoning-budget problem. This needs a paired eval on the 5 failing items plus controls.
- Few-shot: the only evidence found is dottxt's single structured example, which helped the structured format. A single in-schema example of a two-sided comparison answer is a cheap, testable addition (prompt version bump).

### Gaps
- No 2024-2026 study found that directly measures quote-first/extract-then-answer on RAG completeness with numbers. Anthropic's guidance gives no figure.
- No published accuracy-vs-effort curve for RAG answering specifically, or for the GPT-6.x models Vault uses. Aggregate intelligence indexes are a weak proxy.
- No good evidence found on model-size choice for RAG answer generation in 2025-2026 (search budget ran out).
- No evidence found on instructing required elements (dates, units, conditions) with measured effect.

## Query understanding (rewriting, decomposition, HyDE, multi-query/RAG-Fusion)

### Takeaway
Decomposition helps when the question really has several parts, and it helps most when combined with a reranker. Blind rewriting and multi-query fusion often add latency without end-to-end gains. Over-decomposition is common, so the sub-query count should be bounded by detected structure (number of entities or parts), not fixed.

### Cited Findings
- VERIFIED. Ammann et al. 2025, MultiHop-RAG retrieval (bge-large, bge-reranker-large, Qwen2.5-32B, k=10, ≤5 sub-queries): Hits@10 naive 0.781 → +QD 0.810 → +reranker 0.781 → QD+reranker 0.872; MRR@10 0.464 → 0.498 → 0.574 → 0.635. HotpotQA answer F1: 31.3 naive → 32.3 QD → 32.9 rerank → 35.0 QD+rerank. Supporting-fact EM fell slightly with QD (18.4 → 17.0). — [arXiv 2507.00355](https://arxiv.org/html/2507.00355v1)
- VERIFIED. Same paper, retrieval latency per query: naive 0.03 s, +reranker 0.88 s, +QD 16.7 s, QD+reranker 18.9 s (local 32B model on A100; not representative of hosted small models). — [arXiv 2507.00355](https://arxiv.org/html/2507.00355v1)
- CLAIMED (search summary). The same paper reports the LLM produced the maximum ~5 sub-queries for 93.3% of MultiHop-RAG and 98.6% of HotpotQA questions regardless of hop count, i.e. over-splitting. — [alphaxiv overview of 2507.00355](https://www.alphaxiv.org/overview/2507.00355v1)
- CLAIMED (search summary of the paper). Wang et al., "Searching for Best Practices in RAG" (EMNLP 2024): hybrid search + HyDE gave the best retrieval, but at "a huge latency increase"; query rewriting and query decomposition did not improve results in their setup; one hypothetical document is a good cost/benefit point for HyDE. — [ACL Anthology 2024.emnlp-main.981](https://aclanthology.org/2024.emnlp-main.981.pdf)
- CLAIMED (search summary). Medrano et al. 2026, RAG-Fusion in an industry deployment: average overhead 0.89 s, and "retrieval-level improvements do not reliably translate into end-to-end gains". — [arXiv 2603.02153](https://arxiv.org/html/2603.02153v1)
- CLAIMED (search summary). RQ-RAG (trained refiner/decomposer) reports +22.6% average over its baselines on HotpotQA, 2Wiki, MuSiQue; Collab-RAG reports a fine-tuned 3B decomposer beating a frozen 32B LLM at decomposition. — [RQ-RAG arXiv 2404.00610](https://arxiv.org/pdf/2404.00610); [Collab-RAG arXiv 2504.04915](https://arxiv.org/html/2504.04915v1)

### Inferences
- Recommended: structure-driven decomposition only. The query-analysis call already exists, so have it return typed `entities[]`/`sub_questions[]` (bounded, e.g. ≤4). Code then issues one retrieval per entity/sub-question in parallel and keeps the reranker. No HyDE and no blind multi-query: HyDE adds a generation call before retrieval, which conflicts with the TTFT goal, and the evidence for fusion's end-to-end benefit is weak.
- Because sub-queries come out of the existing analysis call, the main added cost is parallel retrieval (~0.6 s per branch if run concurrently, close to zero extra wall time), not an extra LLM call.

### Gaps
- No per-query-type (comparison vs inference vs temporal) breakdown in Ammann et al., so the gain for compare questions specifically is not quantified.
- Wang et al. figures came from a summary, not the paper table.

## Multi-document compare / aggregation (per-document retrieval, map-reduce, entity coverage)

### Takeaway
Cross-document multi-entity questions are a known weak spot: current models miss entities and attributes. The best-supported fix is to enforce coverage in code: retrieve per entity (a per-entity quota in the evidence set) and check coverage before answering, rather than relying on one global top-k.

### Cited Findings
- CLAIMED (abstract via search). MEBench (4,780 cross-document multi-entity questions, 3 categories / 8 types, EA-F1 metric): "even advanced models achieve only 59% accuracy"; models "frequently fail to locate all entity and their attributes". — [MEBench arXiv 2502.18993](https://arxiv.org/html/2502.18993v3)
- CLAIMED (search summary). PluriHop (exhaustive, recall-sensitive questions over distractor-rich corpora): current approaches reach "at most 40% statement-wise F1". — [arXiv 2510.14377](https://arxiv.org/html/2510.14377v1)
- CLAIMED (search summary). MuDABench (ACL 2026 Findings) uses intermediate-fact coverage as a diagnostic for multi-document analytical QA. — [ACL 2026 findings 341](https://aclanthology.org/2026.findings-acl.341.pdf)
- VERIFIED. Speculative RAG drafts answers in parallel over document subsets with a small model, then a large model verifies once: up to +12.97% accuracy and −50.83% latency on PubHealth versus conventional RAG (ICLR 2025). This is the closest evidence for a parallel map step. — [arXiv 2407.08223](https://arxiv.org/abs/2407.08223v2)

### Inferences
- Recommended for Vault's "missing one side" failures: (1) query analysis emits the compared entities/documents as typed fields; (2) retrieval guarantees a minimum of k evidence chunks per entity (a quota-based merge, not one global top-k); (3) the evidence block given to generation is grouped by entity; (4) after generation, code checks that each entity has ≥1 resolved citation; if one is missing, emit a reason code (e.g. a coverage-gap code) and either do one targeted repair call or explicitly say that side was not found. All of this is code-owned control flow, which fits Vault's hard rules.
- Full map-reduce (one LLM call per document, then synthesis) adds a sequential LLM stage. It is justified only for "all documents"/aggregation questions over many documents, not for 2-3 entity compares.

### Gaps
- No benchmark found that isolates "compare A vs B" questions with per-side coverage numbers. MEBench per-type figures were not retrieved.
- No head-to-head measurement found of per-entity retrieval quotas versus global top-k.

## Numeric reasoning over tables (program-of-thought / calculator vs plain generation)

### Takeaway
Executing arithmetic in code beats model arithmetic by a large margin on financial table QA (PoT vs CoT: +24 points FinQA, +19 ConvFinQA, +7.6 TAT-QA with Codex). For Vault, the safe version is narrow: the model returns operands as evidence handles plus an operation, and code computes the result.

### Cited Findings
- VERIFIED. Program of Thoughts (Chen et al., TMLR 2023), few-shot greedy, Codex: FinQA CoT 40.4 vs PoT 64.5; ConvFinQA 45.6 vs 64.6; TAT-QA 61.4 vs 69.0. With self-consistency: FinQA 44.4 vs 68.1; ConvFinQA 47.9 vs 67.3; TAT-QA 63.2 vs 70.2. No zero-shot or GPT-3.5 numbers on these datasets. — [arXiv 2211.12588](https://ar5iv.labs.arxiv.org/html/2211.12588)
- CLAIMED (secondary). FinanceReasoning benchmark summary states PoT significantly outperforms CoT on financial reasoning. — [alphaxiv FinanceReasoning](https://alphaxiv.org/benchmarks/beijing-university-of-posts-and-telecommunications/financereasoning)
- CLAIMED (search summary). DocMath-Eval (ACL 2024) found that harder multi-table settings remain far below expert performance for both CoT and PoT. — [ACL 2024.acl-long.852](https://aclanthology.org/2024.acl-long.852.pdf)
- VERIFIED. OpenAI latency guide: "Compute known values in code." — [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)

### Inferences
- The PoT numbers are from 2022-era non-reasoning models. Reasoning models likely shrink the gap, but no 2025-2026 measurement was found. The value of code execution for Vault is also auditability: a computed number with cited operands can be explained from reason codes.
- Recommended: a typed `calculation` segment (operation from a fixed enum such as diff, ratio, pct_change, sum; operand handles; unit) that code evaluates. No free-form Python execution, since that would be a new service and a sandbox risk.

### Gaps
- No 2024-2026 numbers found for PoT vs plain generation with reasoning models on FinanceBench/TAT-QA.

## Latency engineering (streaming, speculative retrieval, parallelism, prompt caching, routing/cascades, semantic caching, predicted outputs, TTFT targets)

### Takeaway
For a pipeline of sequential calls, the biggest wins come from cutting or overlapping calls on the critical path: stream the answer, start retrieval in parallel with query analysis using the raw query, overlap or defer the support check, and put a stable prefix first for prompt caching. Semantic caching of answers is risky, and Vault forbids it anyway. Predicted outputs do not fit.

### Cited Findings
- VERIFIED. OpenAI latency principles: fastest model that meets the quality bar; fewer output tokens; fewer input tokens with stable content first for caching; make fewer requests; parallelize independent lookups ("Wait for required checks before showing results"); stream with `stream: true`; don't use an LLM when code can answer. Measure TTFT and end-to-end at p50/p95. No quantified gains on the page. — [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)
- VERIFIED. OpenAI prompt caching (current docs): cached input discounted "up to 95%"; on GPT-6.1 Sol and GPT-6.1 Terra cache reads cost 0.05x input; on GPT-5.6+ cache writes cost 1.25x (30-minute TTL) or 2.5x (12-hour TTL, Responses API only); minimum 1,024 tokens on GPT-5.6+; implicit breakpoint at the end of the latest eligible message, or up to 4 explicit breakpoints; `prompt_cache_key` used for accounting on GPT-5.6+ and for routing on earlier models (~15 req/min per key). The docs say caching cuts "the time spent processing input" but give no latency percentage. — [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- VERIFIED. Speculative RAG: parallel small-model drafts plus one large-model verification gave −50.83% latency and up to +12.97% accuracy on PubHealth. — [arXiv 2407.08223](https://arxiv.org/abs/2407.08223v2)
- CLAIMED (vendor blog). RouteLLM (GPT-4 Turbo vs Mixtral 8x7B): cost reductions of over 85% on MT Bench, 45% on MMLU, 35% on GSM8K while keeping ~95% of GPT-4 quality. Savings were measured against a random-routing baseline. — [LMSYS RouteLLM blog](https://lmsys.org/blog/2024-07-01-routellm/)
- CLAIMED (paper abstract, not re-fetched this session). FrugalGPT cascades: match the best single LLM with up to 98% cost reduction. Different setup, not comparable to RouteLLM. — [FrugalGPT arXiv 2305.05176](https://arxiv.org/abs/2305.05176)
- CLAIMED (vendor/practitioner, no peer review). Semantic caching false-positive rates vary widely: a banking FAQ test reported up to 99% false positives at a 0.7 similarity threshold, 27.2% at 0.9 with e5-large-v2, and 3.8% at best with Instructor-large. Failures are silent. — [InfoQ](https://www.infoq.com/articles/reducing-false-positives-retrieval-augmented-generation); [ZeroEntropy](https://www.zeroentropy.dev/concepts/semantic-cache/)
- CLAIMED (secondary + partial OpenAI docs). Predicted Outputs: up to ~5x speedup when most of the output is known in advance (e.g. code edits), 5.8x in a Copilot Workspace benchmark. Supported only on GPT-4o/4.1 families; no function calling, no `max_completion_tokens`; rejected prediction tokens are billed. — [OpenAI predicted outputs](https://developers.openai.com/api/docs/guides/predicted-outputs/index.html); [Neowin](https://www.neowin.net/amp/openais-predicted-outputs-feature-can-speed-up-gpt-4o-model-output-by-up-to-5x)
- CLAIMED (vendor launch post, not re-fetched this session). Anthropic prompt caching: "reducing costs by up to 90% and latency by up to 85% for long prompts". — [Anthropic prompt caching announcement](https://www.anthropic.com/news/prompt-caching)

### Inferences (ranked for Vault, by expected TTFT gain over cost)
1. Overlap query analysis with retrieval: start hybrid retrieval on the raw query at t=0, in parallel with the ~2 s analysis call; when analysis returns, run only the extra per-entity retrievals for compare questions. This can take roughly 0.6 s off the critical path for simple queries. Better still, if the analysis call can be cut down (fewer output fields, `fast` role, minimal effort where supported), it is the largest single block before TTFT.
2. Stream generated segments as they complete, and run the support check per segment or after streaming, with a visible "verifying" state. Following OpenAI's "wait for required checks before showing results", the design choice is whether unverified text may be shown. If not, stream the verification status alongside rather than holding all text back.
3. Prompt caching: put system prompt, schema, and few-shot example first (static, ≥1,024 tokens) and evidence after. Gains are mainly cost (0.05x reads on Terra/Sol) plus some input-processing time. Input-side TTFT is likely a small share at Vault's prompt sizes, so measure `cached_tokens` and TTFT with and without a hit before claiming a gain.
4. Routing: Vault already routes by role in code. A cascade (small model first, escalate on low support score) adds sequential latency on escalations; use it only if cost, not latency, is the target.
5. Do not use: semantic answer caching (Vault rule: answers are never cached; silent false positives), predicted outputs (wrong model families, no structured or tool use, output not known in advance), HyDE (adds a pre-retrieval generation call).

### Gaps
- No reliable published TTFT target for RAG chat found. Common industry folklore (~1 s to first token) could not be sourced this session.
- No measured latency gain from OpenAI automatic caching in the current docs. The older "up to 80% latency" claim could not be re-verified.
- No public engineering numbers from Perplexity, Glean, Hebbia, or Vercel retrieved (search budget).
- No data on whether running the support check in parallel with streaming changes user-perceived trust or error exposure.
