# Architectures for high-accuracy QA over long structured documents (2024-2026)

Context for the reader: Vault is at recall@8 0.73 and answer correctness 0.64 on CUAD, QASPER and FinanceBench. 8 of its 16 failures are retrieval misses on scientific papers and 10-K tables. Confidence tags: VERIFIED = read in the primary source during this session; CLAIMED = search-summary or secondary source, primary not opened; VENDOR = vendor's own claim.

## Q1. What do benchmarks report (FinanceBench, DocFinQA, CUAD/LegalBench-RAG, QASPER, FRAMES), and with what methods?

### Takeaway
On FinanceBench, single-shot vector RAG scores far below full-document long context (about 19-24% vs about 76-79% in the original setups). The only reported results above 90% come from agentic retrieval over the whole filing (search/find/open tools, multi-query), which lands within about 2 points of an oracle that is given the gold page. On CUAD (LegalBench-RAG), raw snippet recall is low even at large k. QASPER answer-F1 numbers are low and not comparable across setups, so "90-99%" is not a meaningful target for QASPER-style F1.

### Cited Findings
**FinanceBench**
- AgenticRAG (arXiv 2605.05538): FinanceBench N=150 over 84 filings (10-K/10-Q/8-K/earnings), average about 143 pages and 116,715 tokens per document. Answer correctness: Traditional RAG 24.24%; keyword-tool agent (pdfgrep/rga, from Subramanian et al.) 32.71%; **AgenticRAG GPT-5-mini 92.00%, Claude Sonnet 4.5 91.78%; oracle (gold full-page evidence + GPT-5-mini) 94.00%**. Scored by an LLM judge plus manual review. VERIFIED (HTML read). The Traditional RAG baseline's configuration is not described. — [AgenticRAG, arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)
- Original FinanceBench baselines as summarized secondhand: GPT-4-Turbo with a shared vector store about 19%; long-context GPT-4-Turbo about 78-79%; Claude 2 with the full filing 76%; prompt ordering alone moved GPT-4-Turbo long-context accuracy from 25% to 78%. CLAIMED (secondary sources disagree on 76/78/79). — [Emergent Mind summary](https://www.emergentmind.com/topics/financebench); [Emergent Mind dataset page](https://www.emergentmind.com/topics/financebench-dataset)
- Finance-adapted embeddings: query rewriting plus filename prepending reached 47%, and better (finance-adapted) embeddings raised it to 55%. CLAIMED. — [Greenback Bears and Fiscal Hawks, arXiv 2411.07142](https://arxiv.org/pdf/2411.07142)
- Vendor (Dewey) reports Claude Opus 4.6 at 87.3% through its agentic retrieval endpoint, above its full-document baseline, and GPT-5.4 at 80.0%. VENDOR + CLAIMED (page fetch failed with a TLS error, figures come from the search snippet). — [Dewey blog](https://meetdewey.com/blog/financebench-eval)
- Practitioner reports: about 76% on the 150 public questions (GitHub project); one write-up went "from 10% to 57%". CLAIMED, not peer-reviewed. — [aquib8112/FinanceBench_RAG](https://github.com/aquib8112/FinanceBench_RAG); [dev.to](https://dev.to/joaopaulotr/from-10-to-57-accuracy-on-financebench-what-actually-moved-the-needle-4iaa)
- FinSage (2025 agentic financial RAG) reported at 70%. CLAIMED (secondhand via the Dewey snippet). — [Dewey blog](https://meetdewey.com/blog/financebench-eval)

**DocFinQA**
- DocFinQA (ACL 2024 short paper) extends FinQA to full documents: about 7.4k questions (7,437; one version says 7,621), average context grows from under 700 words to 123k words. Retrieval pipelines beat retrieval-free long-context models, and models struggle most on the longest documents. A survey says GPT-4's error rate roughly doubled going from FinQA snippets to full DocFinQA documents. CLAIMED (results tables not read). — [ACL Anthology](https://aclanthology.org/2024.acl-short.42/); [PDF](https://aclanthology.org/2024.acl-short.42.pdf)

**CUAD / LegalBench-RAG**
- LegalBench-RAG: 6,858 expert-annotated query-answer pairs over a corpus of more than 79M characters (CUAD, ContractNLI, MAUD, PrivacyQA). Scoring is character-level snippet precision/recall. Embeddings are OpenAI text-embedding-3-large. VERIFIED (HTML read). — [LegalBench-RAG, arXiv 2408.10343](https://arxiv.org/html/2408.10343v1)
- CUAD recall@k with naive chunking and no reranker: k=1 12.60, k=4 27.92, **k=8 40.70**, k=16 51.02, k=32 64.38, k=64 75.71 (precision@8 4.33). All-datasets recall@8 34.51. VERIFIED. — [arXiv 2408.10343](https://arxiv.org/html/2408.10343v1)
- "Surprisingly, the performance of the Cohere Reranker was inferior compared to not using a reranker." CUAD recall@8 with RCTS + Cohere is 28.67. Caveat: the paper's Table 6 (Naive + Cohere) duplicates Table 5 (RCTS, no reranker) exactly, which looks like a table error in the paper. VERIFIED (including the anomaly). — [arXiv 2408.10343](https://arxiv.org/html/2408.10343v1)
- Follow-up: open SBERT/GTE retrieval can match the OpenAI embedding used in LegalBench-RAG. CUAD file names often don't match query text, so the authors raised the matching threshold to 0.55 for CUAD. CLAIMED. — [Towards Reliable Retrieval in RAG Systems for Large Legal Datasets, arXiv 2510.06999](https://arxiv.org/pdf/2510.06999)

**QASPER**
- LongRAG: using whole documents as retrieval units raised QASPER answer F1 from 22.6 (100 x 200-token units) to 26.3. Not comparable to the original QASPER leaderboard. CLAIMED. — [LongRAG, arXiv 2406.15319](https://arxiv.org/html/arXiv:2406.15319)
- StateRAG preprint reports 45.8 QASPER answer F1 (+5.0 over its baselines). CLAIMED. The SOTA2 leaderboard's top Extract F1 is 54.57 (GLM-4.1V-9B, attention-guided RAG), an extractive subset only. CLAIMED. — [StateRAG](https://arxiv.org/html/2605.25379); [SOTA2 Qasper leaderboard](https://www.sota2.com/research/sota/long-context-question-answering-on-qasper-extract-abstract-bool-f1)
- Free-form F1 stays low even when answers are correct, and models often fail to identify the exact supporting passages. CLAIMED. — [Eugene Yan, Evaluating long-context QA](https://eugeneyan.com/writing/qa-evals/)

**FRAMES** (multi-hop over Wikipedia, so a proxy for decomposition)
- 824 questions needing 2-15 Wikipedia articles each. Naive prompting 40.8%; BM25 with 4 documents 47.4%; **multi-step retrieval and reasoning 66% (Gemini-Pro-1.5-0514)**; oracle retrieval 72.9%. Multi-step retrieval without search-planning instructions reached about 0.52. CLAIMED (HF card plus summaries). — [FRAMES, arXiv 2409.12941](https://arxiv.org/html/2409.12941v3); [HF dataset card](https://huggingface.co/datasets/google/frames-benchmark)

### Inferences
- Vault's CUAD recall@8 is not directly comparable to LegalBench-RAG's character-level recall, but the paper shows CUAD snippet retrieval is hard (40.7% at k=8 with a strong embedder). Pushing CUAD much higher probably requires something other than better embeddings: whole-contract context or agentic find/open.
- The >90% FinanceBench result comes from a design that is not top-k chunks. The agent searches, greps, and opens line windows within a known filing. FinanceBench questions name the company and filing, so "which document" is nearly solved and the hard part is locating the table or line inside a 117k-token filing.
- Under its default design Vault caps out near what "enhanced RAG" reports (about 50-76%). The documented route to more than 90% is agentic in-document navigation, or full-document context for documents that fit.

### Gaps
- Exact DocFinQA accuracy tables (retrieval vs long-context per model) were not read.
- No independent (non-vendor) FinanceBench leaderboard was found. Patronus's original paper numbers were not re-verified from the PDF.
- No reliable 2025-2026 QASPER SOTA under a single consistent protocol was found.

## Q2. Agentic / iterative retrieval and query decomposition: measured gains and cost

### Takeaway
Agentic retrieval gives the largest measured jumps on long financial filings (24% to 92% on FinanceBench) and on reasoning-heavy retrieval (BRIGHT recall@1 8.4% to 49.6%). It costs about 2.6-7.8x the tokens and noticeably more latency. A well-tuned non-agentic pipeline can match it on simpler queries, so the papers that report gains route between the two modes.

### Cited Findings
- AgenticRAG tokens: 114.8K vs 14.7K total tokens per FinanceBench query (7.8x). On BRIGHT, 52.3K vs 20.4K (2.6x). Latency is not reported. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)
- AgenticRAG design elements: tools `search`, `find` (pattern or semantic, up to 2 passages per pattern, about 11K-token cap), `open` (1,800-line, line-numbered windows), `summarize`. Multi-query search takes up to 5 reformulations per call and cut BRIGHT tool calls from 6.79 to 4.79 at comparable recall. Results carry title/filename/type metadata. Forced summarization at 128K tokens keeps the reference IDs of cited candidates. A router sends complex queries to the agent and simple ones to traditional RAG. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)
- BRIGHT recall@1: Claude Sonnet 4.5 agentic 49.6%, GPT-5-mini 43.5%; best embedding baseline (Qwen) 27.8%; ReDI 26.0%; single-shot 8.41%. WixQA expert-written factuality 0.96 vs E5 0.85 / BM25 0.83. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)
- "Is Agentic RAG worth it?": agentic used about 3.3x input tokens, 1.9x output tokens and 1.5x time. A well-tuned enhanced RAG can match or beat agentic while staying cheaper. CLAIMED. — [arXiv 2601.07711](https://arxiv.org/html/2601.07711v2)
- Search-R1-style agentic multi-hop needs about 16-22x the inference time of naive single-step RAG. CLAIMED. — [LatentRAG, arXiv 2605.06285](https://arxiv.org/pdf/2605.06285)
- "Stronger Baselines for RAG" (EMNLP 2025): simpler structured approaches can outperform multi-stage pipelines such as ReadAgent, which spend more tokens and latency. CLAIMED. — [ACL Anthology](https://aclanthology.org/2025.emnlp-main.1656.pdf)
- FRAMES: multi-step retrieval with planning instructions 0.66 vs 0.52 without planning vs 0.474 single-shot BM25. Planning prompts matter. CLAIMED. — [arXiv 2409.12941](https://arxiv.org/html/2409.12941v3)
- Keyword-only agent tools (pdfgrep/rga) reached only 32.71% on FinanceBench. Agent tools need semantic search plus structured windows, not just grep. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)

### Inferences
- For Vault, a bounded "locate within document" loop (search, then find, then open a window, with typed tool decisions applied by code) is consistent with the project's rule that the LLM returns typed decisions and code applies them. Gate it behind a router on query type or on a low-confidence signal, since it costs about 3-8x tokens.
- Multi-query (up to 5 reformulations) is a cheap component that can be tested on its own before building a full agent.

### Gaps
- No measured latency for AgenticRAG on FinanceBench.
- No agentic results on QASPER or CUAD specifically.

## Q3. Long context (with prompt caching) vs RAG for single/few documents

### Takeaway
For strong proprietary models, putting the full document in context beats chunk RAG on structured documents (papers, financial statements) and especially on comparison/multi-chunk questions. RAG wins on hallucination detection and for weaker or open models at 128k. Anthropic's guidance is to skip RAG below about 200k tokens. Databricks found FinanceBench accuracy still rising up to 128k tokens of retrieved context for top commercial models.

### Cited Findings
- LaRA setup: 2,326 cases; novels, academic papers, financial statements; RAG = 600-token chunks, 5 chunks, 100 overlap, GTE-large + BM25 hybrid. VERIFIED. — [LaRA, arXiv 2502.09977](https://arxiv.org/html/2502.09977v1)
- LaRA GPT-4o LC/RAG accuracy (%) at 32k: location 86.33/82.55, reasoning 75.48/68.51, **comparison 79.66/46.56**, hallucination 72.30/78.39. At 128k: location 87.70/82.22, reasoning 79.35/70.26, comparison 64.74/40.84, hallucination 56.34/67.33. VERIFIED (table read; column order inferred as LC/RAG, consistent with the text). — [arXiv 2502.09977](https://arxiv.org/html/2502.09977v1)
- LaRA averages across models of LC minus RAG: at 32k +0.08 location, +4.66 reasoning, +15.22 comparison, -10.38 hallucination; at 128k -5.25, -1.39, +14.30, -22.36. Weak models collapse in LC (Mistral-Nemo-12B location 23.44 LC vs 72.29 RAG at 128k). The paper says LC leads for highly structured texts like papers and financial statements. VERIFIED. — [arXiv 2502.09977](https://arxiv.org/html/2502.09977v1)
- Anthropic: "If your knowledge base is smaller than 200,000 tokens (about 500 pages worth of material), you can just include the entire knowledge base in the prompt." Prompt caching is described as "reducing latency by > 2x and costs by up to 90%." VENDOR, VERIFIED. — [Anthropic, Contextual Retrieval](https://anthropic.com/research/contextual-retrieval)
- Databricks (Leng et al., arXiv 2411.03538): about 20 LLMs, 2k to 128k (up to 2M) context. NQ saturates at 8k, DocsQA at 96k, **FinanceBench at 128k**. The best commercial models (o1, GPT-4o, Claude 3.5 Sonnet) improve steadily with context length, while most open models rise and then fall. CLAIMED (abstract/summary level). — [arXiv 2411.03538](https://arxiv.org/pdf/2411.03538); [Databricks blog](https://databricks.com/blog/long-context-rag-performance-llms)
- AgenticRAG's FinanceBench documents average 116,715 tokens, so a single 10-K fits within a 128k-200k window. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)

### Inferences
- Vault's corpus units (one contract, one paper, one 10-K) are mostly under 200k tokens. A "full-document mode" for single-document questions, with prompt caching, is the evidence-backed option, especially for comparison/multi-part questions where LaRA shows a gap of about 15 points. The tension with Vault is citations: full-context answers still need to emit sentence handles. That works only if the document is serialized with handles inline, which multiplies the token count. This is an inference, not measured.
- Keep RAG (or the hybrid) for hallucination-prone and unanswerable questions, where LaRA shows RAG ahead.

### Gaps
- No 2025-2026 head-to-head of cached full-document vs RAG on FinanceBench using current frontier models was verified, beyond the vendor Dewey claim.
- No measured cost/latency of handle-annotated full-document prompts.

## Q4. Table-aware parsing and structure-aware chunking: effect on financial QA

### Takeaway
Evidence is mostly vendor or blog benchmarks. On complex financial tables, LlamaParse and Docling lead on table-structure accuracy (about 83-91%) over Unstructured hi_res (71%). Downstream retrieval differences are smaller (recall@5 72-78%). LegalBench-RAG found chunking strategy mattered more than reranking. There is no strong independent study tying parser choice to FinanceBench accuracy.

### Cited Findings
- Kanopy Labs (blog, 200 financial PDFs): table structure accuracy LlamaParse 89%, Docling 83%, Unstructured hi_res 71%. RAG recall@5: Docling structural chunking 78%, LlamaParse 76%, Unstructured 72%. CLAIMED (blog). — [Kanopy Labs](https://kanopylabs.com/blog/unstructured-vs-llamaparse-vs-docling-document-parsing)
- On a 50-page 10-K, LlamaParse 91.4% table F1 (paid cloud API). CLAIMED (blog). — [aiagentskit](https://aiagentskit.com/blog/docling-vs-marker-vs-mineru-vs-llamaparse/)
- Docling 0.911 TEDS on 200 PDFs. Reported by a competitor (pdfmux). VENDOR-adjacent. — [pdfmux](https://pdfmux.com/blog/pdfmux-vs-llamaparse-vs-docling-vs-unstructured-2026/)
- Unstructured's own benchmark (1,000+ enterprise pages, vs Reducto, LlamaParse, Docling and others) ranks its VLM configurations first. VENDOR. — [Unstructured benchmarks](https://unstructured.io/benchmarks)
- VLM or agentic parsers reportedly gain about 20% extraction accuracy on hard financial inputs, while clean digital PDFs are handled well by Docling. CLAIMED (blog). — [Particula](https://particula.tech/blog/document-parsing-rag-reducto-llamaparse-unstructured-docling)
- Academic: the UDA benchmark measures how table-parsing choices affect downstream RAG QA and notes that financial numeric answers are hard to assess. CLAIMED. — [UDA, arXiv 2406.15187](https://arxiv.org/pdf/2406.15187)
- LegalBench-RAG: chunking strategy is a primary driver of retrieval, and the reranker hurt. VERIFIED (reranker quote). — [arXiv 2408.10343](https://arxiv.org/html/2408.10343v1)

### Inferences
- Since half of Vault's misses are 10-K tables, the cheapest test is table-as-unit chunking (one chunk per table, with the caption/section heading plus header rows repeated on every split). That can be tested inside the existing pdfplumber/glyph pipeline without adopting a new parser, which also respects invariant I1 (evidence only from the glyph-mapped stream). Adopting Docling or LlamaParse would conflict with I1 unless their output is mapped back to glyph offsets.

### Gaps
- No peer-reviewed study reporting FinanceBench answer accuracy as a function of parser was found.
- Azure Document Intelligence, Reducto and Marker numbers were not independently verified.

## Q5. Late interaction / visual retrieval, LLM rerankers, contextual retrieval, HyDE / multi-query

### Takeaway
Contextual retrieval (chunk-level LLM-written context, prepended for both embedding and BM25) plus reranking is the best-documented retrieval-side gain: top-20 failure rate 5.7% to 1.9% (VENDOR). ColPali/ColQwen beat OCR+text retrieval by about 15+ nDCG points on visually rich pages, but OCR baselines were weak, and born-digital text is competitive. LLM listwise rerankers roughly match strong cross-encoders on BEIR at much higher cost. Off-the-shelf rerankers can hurt in legal (LegalBench-RAG).

### Cited Findings
- Anthropic Contextual Retrieval (metric = 1 - recall@20): baseline 5.7%; contextual embeddings 3.7% (-35%); + contextual BM25 2.9% (-49%); + reranking 1.9% (-67%). The rerank stage took the top 150 down to 20. Top-20 beat top-5/10. The context is "usually 50-100 tokens". One-time cost "$1.02 per million document tokens" (800-token chunks, 8k-token documents, with prompt caching). VENDOR, VERIFIED. — [Anthropic](https://anthropic.com/research/contextual-retrieval)
- Critique: the 67% is a narrow retrieval-failure metric, not end-task accuracy. CLAIMED (blog). — [Almond AI on Medium](https://medium.com/almond-ai/responding-to-anthropics-contextual-retrieval-for-rag-apps-why-context-is-not-all-you-need-af503985aa55)
- ColPali on ViDoRe v1: 81.3 average nDCG@5 vs OCR+BM25 65.5 and OCR+BGE-M3 66.1 (best text+OCR+captioning 67.0). ColQwen2.5-v0.2 89.5 vs ColPali-v1.3 84.8 in a later re-evaluation, under a different setup. CLAIMED. — [Emergent Mind ColPali](https://www.emergentmind.com/papers/2407.01449); [MINER, arXiv 2605.06460](https://arxiv.org/pdf/2605.06460)
- Caveats: ViDoRe documents are clean, the ColPali OCR baseline used Tesseract (not state of the art), and OCR pipelines stay competitive on born-digital text. Multi-vector storage cost grows with the number of patches. CLAIMED. — [Lost in OCR Translation?, arXiv 2505.05666](https://arxiv.org/pdf/2505.05666); [Mixpeek comparison](https://mixpeek.com/comparisons/colpali-vs-ocr)
- RankGPT-style listwise LLM reranking is competitive with cross-encoders on TREC-DL/BEIR but costs far more. In-domain-trained cross-encoders can win. LLM rerankers are stronger distillation teachers. CLAIMED. — [FIRST, arXiv 2406.15657](https://arxiv.org/html/2406.15657v1); [PE-Rank, arXiv 2406.14848](https://arxiv.org/pdf/2406.14848); [ZeroEntropy concepts](https://zeroentropy.dev/concepts/listwise-reranking/)
- Multi-query reformulation (up to 5 per call) inside an agent reduced tool calls at comparable recall. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)
- Filename prepending plus query rewriting helped FinanceBench RAG (reaching 47%). CLAIMED. — [arXiv 2411.07142](https://arxiv.org/pdf/2411.07142)

### Inferences
- Vault already has hybrid BM25 + dense RRF. Contextual BM25 + contextual embeddings is the step closest to it. Vault's rule that "prefixes are never cited" fits: the context prefix is indexed but not evidence.
- MiniLM cross-encoders are small. The LegalBench-RAG reranker result suggests measuring before keeping one, and an LLM listwise rerank of the top 30-50 is a plausible upgrade on the hard splits only.
- ColPali-style visual retrieval mainly helps scanned or visually complex pages. Vault's PDFs (10-Ks, arXiv papers) are mostly born-digital, so the expected gain is lower than ViDoRe suggests. This is speculation.

### Gaps
- No verified HyDE numbers on these benchmarks this session.
- No ColBERT (text late interaction) results on FinanceBench/QASPER found.

## Q6. Open-source reference implementations and what makes them accurate

### Takeaway
PaperQA2 is the best-documented accurate open system for papers. Its precision (85.2%) beats experts, but its accuracy (66.0%) only matches them. Its key ingredients are agentic search, deep LLM re-scoring of retrieved chunks with summarization (RCS), and citation traversal, at roughly $1-3 per query. No benchmark numbers for RAGFlow, Onyx, Kotaemon or R2R were found.

### Cited Findings
- PaperQA2 on LitQA2 (248 multiple-choice questions, answers appear once in paper main text): precision 85.2% ± 1.1% vs humans 73.8% ± 9.6%; accuracy 66.0% ± 1.2% vs humans 67.7% ± 11.9% (not significantly different). The original PaperQA scored 36.7% accuracy / 76.5% precision. CLAIMED (consistent across multiple summaries; the primary PDF was not opened). — [arXiv 2409.13740](https://www.alphaxiv.org/abs/2409.13740); [Emergent Mind](https://www.emergentmind.com/papers/2409.13740)
- "A deep (>10) RCS ranking list with a high-performing LLM is crucial in achieving human-level accuracy". Cost estimated at about $1-3 per query. CLAIMED (secondary). — [Moonlight review](https://www.themoonlight.io/en/review/language-agents-achieve-superhuman-synthesis-of-scientific-knowledge); [cognitivetech summary](https://github.com/cognitivetech/llm-research-summaries/blob/main/scientific-research/Language_Agents_Science.md)
- AgenticRAG (not open source as far as verified) shows the in-document navigation pattern: search, find, open, summarize. VERIFIED. — [arXiv 2605.05538](https://arxiv.org/html/2605.05538v1)

### Inferences
- PaperQA2's RCS step (an LLM scores and summarizes each of the top N > 10 chunks for relevance before answering) is an LLM rerank-plus-filter. It is the component most transferable to Vault's QASPER misses. Its summaries must not become evidence. Cite the underlying sentences.
- High precision with moderate accuracy reflects abstention. A "can't find it" outcome, rather than a guess, is part of how these systems report high precision.

### Gaps
- No quantitative benchmark results were found for RAGFlow, Onyx, Kotaemon or R2R. Their accuracy claims are unverified.
- No reviewed system reports 90-99% on QASPER or CUAD. The over-90% numbers found are FinanceBench under an agentic harness (about 92%) and FinanceBench oracle (94%).
