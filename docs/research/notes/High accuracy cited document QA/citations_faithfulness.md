# Precise citations, faithfulness checking, self-verification and calibrated abstention in document QA (2023-2026)

Context for the writer: Vault already copies cited text in code from sentence handles, so citation integrity is 1.00. It runs a per-segment support check with a cheap model. Its evidence gate falsely refused 3 of 44 answerable questions (6.8%). Faithfulness is 0.84 and citation chunk precision is 0.62. Confidence tags: VERIFIED means read in the primary source this session. CLAIMED means a vendor or secondary summary. UNKNOWN means not found.

## Q1. How vendor APIs produce fine-grained citations, and whether any precision/recall numbers are published

### Takeaway
The vendors converge on Vault's design: the server pre-chunks documents into sentences or caller-defined blocks, the model points at chunks, and code copies the text. No vendor publishes citation precision or recall on a public benchmark. The only numbers are vendor or customer claims (Anthropic: "up to 15%" recall; Endex: source confabulations down from 10% to 0%).

### Cited Findings
- **Anthropic Citations API (VERIFIED, docs).**
  - "Document contents are 'chunked' to define the minimum granularity of possible citations... sentence chunking lets Claude cite a single sentence or chain together multiple consecutive sentences."
  - PDFs and plain text are chunked into sentences automatically. Custom content documents are "used as-is and no further chunking is done", and they cite by block index range.
  - Because the API "extracts `cited_text` directly, citations are guaranteed to contain valid pointers to the provided documents."
  - The API does not guarantee that a field has at least one citation.
  - "Citations and structured outputs are incompatible": combining them returns a 400 error.
  - The docs advise: to cite specific sentences from RAG chunks, put each chunk in a plain-text document; for custom granularity, use custom content.
  - [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- **Anthropic vendor claims (CLAIMED).**
  - Internal evals found built-in citations beat most custom implementations, "increasing recall accuracy by up to 15%."
  - Endex reported that source confabulations and formatting issues fell from 10% to 0%, with 20% more references per response.
  - `cited_text` does not count toward output tokens.
  - [Anthropic announcement](https://claude.com/blog/introducing-citations-api)
- **Google Vertex AI Check Grounding API (VERIFIED, docs; CLAIMED latency).**
  - Returns an overall support score from 0 to 1, plus citations from each claim to the supporting facts.
  - "In this version of the API, a sentence is considered a single claim."
  - `citationThreshold` is a 0-1 float, default 0.6. A higher value gives fewer but stronger citations.
  - Optional claim-level support scores are available.
  - Stated latency is under 500 ms.
  - Anti-citations (contradiction detection) and a helpfulness score are experimental and allowlist-only.
  - [Google Check Grounding docs](https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding)
- **OpenAI file search (VERIFIED, docs plus community).**
  - A `file_citation` annotation carries `file_id`, `filename`, `index` and `type`. It points at a file, not a chunk or span.
  - Developers report "no longer any way to determine which specific chunk was referenced" in the Responses API.
  - Official docs and developers disagree on what `index` means.
  - [OpenAI file search guide](https://developers.openai.com/api/docs/guides/tools-file-search); [community thread](https://community.openai.com/t/file-search-annotations-reference-a-file-rather-than-a-chunk/1367438); [community thread 2](https://community.openai.com/t/how-can-i-correlate-inline-citations-with-file-search-results/1335592)
- **Cohere RAG citations (VERIFIED, docs; tradeoff CLAIMED).**
  - `citation_options.mode` is `accurate` by default: citations are produced after the full response.
  - `fast` produces citations inline "at the expense of slightly less precision in citation relevance."
  - [Cohere RAG citations docs](https://docs.cohere.com/docs/rag-citations)

### Inferences
- Vault's handle-plus-code-copy design matches Anthropic's sentence-chunk pointer design. Integrity 1.00 is table stakes, not a differentiator.
- The open levers are precision (which sentences get cited) and support (does the cited span entail the claim), which Vault measures at 0.62 and 0.84.
- Google's design separates generation from post-hoc claim-to-fact citation with a tunable threshold (default 0.6). A post-hoc pruning pass like this is a cheap way to raise Vault's citation precision: score each (segment, cited sentence) pair and drop citations below the threshold.
- Cohere's accurate-vs-fast split suggests that assigning citations after generation is more precise than inline citing. This is a vendor claim with no numbers.
- OpenAI's file-level annotations are too coarse for Vault's needs. If Vault keeps the OpenAI gateway, its own handle schema is the right call.

### Gaps
- No vendor publishes citation precision or recall on ALCE, LongBench-Cite or similar benchmarks.
- I searched for, but did not fetch, any Perplexity citation-accuracy numbers. Independent audits exist (for example, news-citation studies), but they were not verified this session.

## Q2. Attribution benchmarks and metrics (ALCE, AutoAIS, LongCite, AttributionBench) and top scores

### Takeaway
Citation recall means the cited set entails the statement. Citation precision means each individual citation is needed or relevant. Scores for strong models cluster around 65-75 F1. Sentence-level citing beats chunk-level citing by about 14 F1 points on the same model.

### Cited Findings
- **ALCE (Gao et al., 2023).**
  - Measures citation recall (do the cited passages jointly entail the statement) and citation precision (is each citation relevant), using an NLI (AutoAIS-style) judge.
  - On ASQA, ChatGPT VANILLA (5 passages) scored 40.4 EM recall and 73.6 citation recall.
  - SUMM (10 passages) raised correctness to 43.3 but cut citation recall to 68.9.
  - "Citation quality lags behind correctness across all models."
  - [ALCE paper](https://arxiv.org/abs/2305.14627); numbers via a [review summary](https://liner.com/review/enabling-large-language-models-to-generate-text-with-citations) (CLAIMED, not checked against the table)
- **Self-RAG 13B on ASQA (ALCE metrics).** Citation precision 70.3, recall 71.3. Its precision beats ChatGPT. [Self-RAG](https://arxiv.org/abs/2310.11511) (CLAIMED, via search summary)
- **LongBench-Cite (VERIFIED, paper v3, Table 2, LAC-S sentence-level).**

  | Model | Avg citation F1 | Avg cited length (tokens) |
  |---|---|---|
  | GPT-4o | 65.6 | 220 |
  | Claude-3-Sonnet | 67.2 | 132 |
  | GLM-4 | 65.4 | 169 |
  | LongCite-8B | 72.0 | 85 |
  | LongCite-9B | 69.2 | 91 |

  [LongCite](https://arxiv.org/html/2409.02897v3)
- **LongCite granularity (VERIFIED).**
  - On GLM-4, chunk-level citing (128-token chunks) scores F1 51.6. Sentence-level citing scores F1 65.4.
  - Cited length is reported "to avoid trivial hacks such as citing the whole context."
  - Citation SFT raised correctness by 16-28% over long-context SFT without citations. In-context (prompted) citation generation usually lowered correctness.
  - [LongCite](https://arxiv.org/html/2409.02897v3)
- **LongCite precision definition (VERIFIED).**
  - Each citation is scored 0 or 1. It counts as relevant if it "entails some key points of the statement," so partial support counts.
  - Recall is scored 1, 0.5 or 0 for full, partial or no support.
  - GPT-4o is the judge.
  - [LongCite](https://arxiv.org/html/2409.02897v3)
- **AutoAIS evaluator selection.** A 2025 ACL Findings paper argues that choosing an AutoAIS evaluator on balanced accuracy alone hides inconsistencies. [ACL Findings 2025](https://preview.aclanthology.org/display_plenaries/2025.findings-acl.1175.pdf) (CLAIMED, abstract-level)

### Inferences
- Vault's "citation chunk precision 0.62" is probably stricter than ALCE or LongCite precision. Those metrics count partial support as precise, and they are measured per citation, not per chunk.
- Before optimizing, Vault should report precision under the LongCite or ALCE definition alongside its own, and track average cited tokens or sentences per claim. Otherwise the 0.62 can't be compared to the 65-72 F1 range.
- The consistent lesson is that finer citation units raise F1. Vault already cites sentences. Precision loss probably comes from citing several sentences or chunks per segment, not from unit size.

### Gaps
- AttributionBench numbers were not retrieved (my recollection is that fine-tuned NLI models score around 80% or below, but this is unverified).
- I did not verify ALCE citation precision for ChatGPT.

## Q3. Hallucination and faithfulness detectors: accuracy vs GPT-4-class judges, and cost

### Takeaway
Small specialized checkers (MiniCheck, Bespoke-MiniCheck-7B) match or beat GPT-4 on grounded-factuality benchmarks at more than 400x lower cost. On genuinely hard cases (FaithBench), every detector, GPT-4o included, is close to chance.

### Cited Findings
- **LLM-AggreFact (VERIFIED, MiniCheck paper Table 2).** Average balanced accuracy, threshold 0.5, no per-dataset tuning:

  | Checker | Avg BAcc |
  |---|---|
  | GPT-4 | 75.3 |
  | MiniCheck-FT5 (770M) | 74.7 |
  | Claude-3 Opus | 74.1 |
  | Mistral-Large | 73.4 |
  | MiniCheck-RoBERTa | 72.7 |
  | AlignScore | 70.4 |
  | GPT-3.5 | 69.6 |
  | Gemini-Pro | 66.2 |

  [MiniCheck](https://arxiv.org/html/2404.10774v2)
- **Cost on the 13K test set (VERIFIED).**
  - MiniCheck-FT5: about $0.24.
  - GPT-4: about $107, rising to $212 with claim decomposition.
  - Claude-3 Opus: about $165.
  - [MiniCheck](https://arxiv.org/html/2404.10774v2)
- **MiniCheck method (VERIFIED).**
  - Each sentence is checked on its own as a claim, and a claim counts as supported if any document supports it (max over documents).
  - Atomic-fact decomposition costs 2-4x with "no consistent accuracy gain."
  - Decontextualization gave no clear benefit on the benchmark.
  - [MiniCheck](https://arxiv.org/html/2404.10774v2)
- **Bespoke-MiniCheck-7B (CLAIMED, vendor and leaderboard).**
  - 77.4% average on LLM-AggreFact, described as the leaderboard's best at the time, versus 71.8% for Vectara HHEM-2.1.
  - The license is reported as CC BY-NC 4.0 (sources conflict on commercial use).
  - [Bespoke](https://bespokelabs.ai/bespoke-minicheck); [LLM-AggreFact blog](https://llm-aggrefact.github.io/blog); [HF card](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B)
- **RAGTruth, LettuceDetect (CLAIMED, paper abstract).**
  - Example-level F1 79.22, which beats GPT-4-turbo prompting (63.4) and Luna by 14.8 points.
  - Span-level F1 58.93.
  - RAG-HAT (Llama-3-8B with DPO) scores 83.9.
  - LettuceDetect is a ModernBERT encoder with 8K context.
  - [LettuceDetect](https://arxiv.org/abs/2502.17125)
- **FaithBench (CLAIMED, abstract).**
  - Built from the summaries on which detectors disagree most (TrueNLI, TrueTeacher, HHEM-2.1-Open, GPT-4o judge).
  - The best detectors sit near 50% accuracy: best F1-macro about 55, best balanced accuracy about 58.
  - [FaithBench](https://arxiv.org/html/2410.13210v1)

### Inferences
- Vault's per-segment check with a cheap model follows the same recipe as MiniCheck: sentence or segment as the claim, max over cited evidence. The literature says decomposing claims further rarely pays for its cost.
- Upgrade path: a dedicated checker (MiniCheck-FT5 or Bespoke-MiniCheck-7B, served next to the reranker) could replace or supplement the cheap LLM check at near-zero marginal cost. This needs a design-doc justification, since it adds a new service or dependency. Check the license first.
- FaithBench implies a ceiling. On hard borderline segments no detector is reliable, so Vault should calibrate the checker's threshold on its own labeled segments rather than trust a default 0.5.

### Gaps
- No verified head-to-head numbers for Patronus Lynx (vendor claims beat GPT-4o on HaluBench) or Vectara HHEM on RAGTruth.
- No checker latency figures except Google's "under 500 ms" claim.

## Q4. Self-verification and repair loops: measured gains and cost

### Takeaway
Verify-then-revise reliably raises precision of claims, but the measured gains come mostly from closed-book or long-form generation. For grounded RAG, the cheapest proven form is "find a supporting quote for each claim, else delete it," which is Anthropic's documented pattern.

### Cited Findings
- **Chain-of-Verification (CoVe), CLAIMED via summary.**
  - On Wikidata list questions, precision rose from 0.17 (Llama-65B few-shot) to 0.36 with CoVe two-step.
  - On MultiSpanQA, F1 rose from 0.39 to 0.48.
  - Long-form generation saw "even larger gains."
  - The cost is several extra LLM calls (plan questions, answer them, revise).
  - [CoVe review](https://liner.com/review/chainofverification-reduces-hallucination-in-large-language-models); paper arXiv:2309.11495
- **CoV-RAG (EMNLP 2024 Findings).** Adds a verification module that corrects both retrieval errors and generation inconsistencies. Numbers were not retrieved. [CoV-RAG](https://arxiv.org/abs/2410.05801)
- **Self-RAG.** Reflection tokens critique retrieval need, relevance and support. Citation precision on ASQA is 70.3 (13B), higher than ChatGPT. [Self-RAG](https://arxiv.org/abs/2310.11511) (CLAIMED)
- **Anthropic guidance (VERIFIED, docs).**
  - "For tasks involving long documents (>20k tokens), ask Claude to extract word-for-word quotes first before performing its task."
  - "Have Claude verify each claim by finding a supporting quote after it generates a response. If it can't find a quote, it must retract the claim."
  - Also: best-of-N consistency checks, explicitly allowing "I don't know", and restricting answers to the provided documents.
  - [Anthropic: reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)

### Inferences
- For Vault, the support check already exists, so the cheapest repair is a deterministic action on its verdict. Drop or rewrite only the unsupported segments, re-check once, and cap the loop at one pass.
- Full regeneration is costlier and risks new unsupported content.
- This fits the hard rule that the LLM returns typed decisions and code applies them.

### Gaps
- CRAG (Yan et al., 2024) numbers were not retrieved.
- I found no study measuring latency or cost of verify-and-repair specifically in grounded document QA.

## Q5. Why citations cover too many chunks, and how to make models cite minimally

### Takeaway
Over-citation comes from prompted in-context citing with coarse units and no penalty on length. The fixes with evidence are sentence-level units (+14 F1), extract-quotes-first, citation SFT, post-hoc citation assignment or pruning with a support threshold, and measuring cited length.

### Cited Findings
- LongCite explicitly measures cited length because F1 can be gamed by citing everything. With better citation training, cited length fell from 220 tokens (GPT-4o) to 85 (LongCite-8B) while F1 rose. [LongCite](https://arxiv.org/html/2409.02897v3)
- Sentence-level citing beats 128-token chunks by 51.6 to 65.4 F1 on GLM-4. [LongCite](https://arxiv.org/html/2409.02897v3)
- Google's `citationThreshold` (default 0.6) trades citation count for strength: "fewer but stronger citations." [Google Check Grounding](https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding)
- Cohere: post-generation ("accurate") citations are more precise than inline ("fast") citations. [Cohere](https://docs.cohere.com/docs/rag-citations) (CLAIMED)
- Anthropic: extract quotes first, then answer by referencing the quotes by number. [Anthropic](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- Anthropic allows citing consecutive sentences as a range, which makes the minimal span explicit. [Anthropic Citations](https://platform.claude.com/docs/en/build-with-claude/citations)

### Inferences
Candidate changes for Vault, each to be validated through the convergence engine:
- (a) Add a schema field so each segment cites at most k handles, with the prompt saying "cite the minimal set."
- (b) After generation, score each (segment, handle) pair with the support checker and drop handles that aren't needed. Greedy leave-one-out: a handle is redundant if the remaining set still entails the segment. This directly targets the 0.62 precision.
- (c) Report average handles per segment as a guard metric.

### Gaps
- I found no published ablation that isolates "cite minimally" prompt wording.

## Q6. Abstention calibration without false refusals

### Takeaway
Abstaining on insufficient context alone over-refuses. In Google's ICLR 2025 work, models answered correctly 35-62% of the time with insufficient context. Combining a sufficiency signal with model confidence in a small classifier, with the threshold set on a coverage-accuracy curve, gained 2-10% selective accuracy. Fine-tuning for "I don't know" did not reliably help.

### Cited Findings
- **Sufficiency autorater (VERIFIED, 115 human-labeled instances).**

  | Autorater | F1 | Accuracy | Notes |
  |---|---|---|---|
  | Gemini 1.5 Pro, 1-shot | 0.935 | 0.930 | |
  | FLAMe 24B | 0.892 | 0.878 | cheaper; run on 1600-token chunks, "sufficient if any chunk is" |
  | TRUE-NLI | 0.818 | 0.826 | needs a ground-truth answer |

  [Sufficient Context, Joren et al., ICLR 2025](https://arxiv.org/html/2411.06037v2)
- **Insufficient context is common and does not imply a wrong answer (VERIFIED).**
  - 55.4% of Musique instances have insufficient context, about 56% for HotpotQA and about 22.6% for FreshQA.
  - LLMs still answer correctly 35-62% of the time with insufficient context, through parametric knowledge, partial multi-hop inference and disambiguation.
  - [Sufficient Context](https://arxiv.org/html/2411.06037v2)
- **Hallucination rates, sufficient vs insufficient context (VERIFIED).**

  | Model | Sufficient | Insufficient |
  |---|---|---|
  | Claude 3.5 Sonnet | 3.2% | 36.5% |
  | GPT-4o | 12.7% | 15.4% |
  | Gemini 1.5 Pro | 14.3% | 40.4% |

  [Sufficient Context](https://arxiv.org/html/2411.06037v2)
- **Selective generation (VERIFIED).**
  - Logistic regression over the sufficient-context label plus self-rated confidence (P(True) or P(Correct)); abstain below a threshold.
  - Gains: over 10% for Gemma-27B on HotpotQA in high-accuracy regions, over 5% for Gemini 1.5 Pro near 70% coverage, and none for Gemma on Musique (the sufficiency coefficient was 0).
  - Overall: 2-10% more correct answers among responses.
  - [Sufficient Context](https://arxiv.org/html/2411.06037v2)
- **Fine-tuning for abstention fails (VERIFIED).**
  - Llama on Musique: hallucination rose from 26.8% (vanilla) to 36-39%, and abstention fell.
  - Mistral: abstention fell to about 0-2%.
  - [Sufficient Context](https://arxiv.org/html/2411.06037v2)
- **Prompted abstention (VERIFIED, vendor guidance).** Explicitly allowing "I don't know" "can drastically reduce false information" (no number given). [Anthropic](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)

### Inferences
- Vault's evidence gate, at 3 of 44 false refusals, likely behaves like a binary sufficiency classifier. The paper shows that alone is miscalibrated.
- Recommended change:
  1. Turn the gate into a scored signal (sufficiency probability, max retrieval or rerank score, and checker support of a draft answer).
  2. Fit a tiny logistic combiner on train-split questions only.
  3. Pick the threshold from the coverage vs. selective-accuracy curve under an explicit target, for example zero false refusals on answerable train questions subject to a hallucination ceiling.
  4. Accept it only through the paired test decision.
- Prefer "answer and flag partial support" over refusal when sufficiency is borderline. Partial multi-hop and disambiguation cases were a large share of correct answers under insufficient context.
- With n=44, one refusal is 2.3 points. Report a confidence interval and don't tune on the test split.

### Gaps
- No verified numbers for other selective-prediction methods in RAG (for example, conformal abstention or semantic-entropy gating).
- No public benchmark that reports false-refusal rate specifically for document QA.
