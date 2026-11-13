# Grounded citations, abstention, and document parsing for RAG (2025-2026)

Scope note: research done 2026-11-13. Confidence tags: VERIFIED = read in a primary source (vendor doc, paper abstract or paper HTML, GitHub repo/license file) during this session; CLAIMED = vendor or self-reported, or seen only in a secondary summary; UNKNOWN = not found. Vault context: application-managed sentence handles (E3.1), strict JSON schema, code copies stored sentence text, pdfplumber + custom reading-order/table/header-footer logic, OpenAI-only runtime.

## A1. Vendor citation APIs (Anthropic Citations, OpenAI file_search annotations, Google Vertex grounding)

### Takeaway
Anthropic's Citations API is the closest vendor analogue to Vault's design: it chunks documents into sentences, the model emits pointers, and the API copies `cited_text` from the source, so citations are guaranteed to point at real document text. It cannot be combined with structured outputs, though. OpenAI's file_search annotations are much coarser: they give a file id/filename plus an index into the answer, with no quoted span or page. Google's grounding supports map answer spans to chunk indices, but confidence scores are not returned for Gemini 2.5 and later. Vault's handle scheme already matches or beats all three on granularity, and it is the only one of the four that also gives a strict schema.

### Cited Findings
- Anthropic chunking (VERIFIED): "Document contents are 'chunked' to define the minimum granularity of possible citations. For example, sentence chunking lets Claude cite a single sentence or chain together multiple consecutive sentences to cite a paragraph or longer passage." Plain text and PDF are sentence-chunked automatically. "For custom content documents: Your provided content blocks are used as-is and no further chunking is done." — [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Anthropic citation formats (VERIFIED): plain text returns `char_location` (0-indexed character indices, end exclusive), PDF returns `page_location` (1-indexed pages), and custom content returns block indices (0-indexed). Every citation includes `cited_text`. — [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Anthropic reliability and cost (VERIFIED): "Because the API parses citations into the response formats ... and extracts `cited_text` directly, citations are guaranteed to contain valid pointers to the provided documents." Also: "`cited_text` does not count toward your output tokens" and is "also not counted toward input tokens" when passed back. Enabling citations causes "a slight increase in input tokens because of system prompt additions and document chunking." — [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Anthropic limitations (VERIFIED): "Citations cannot be used together with structured outputs ... because citations require interleaving citation blocks with text output, which is incompatible with the strict JSON schema constraints." Only text citations are supported: "PDFs that are scans of documents and do not contain extractable text are not citable." — [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Anthropic launch claims (CLAIMED, vendor internal eval): built-in citations "outperform most custom implementations, increasing recall accuracy by up to 15%." — [Anthropic announcement](https://claude.com/blog/introducing-citations-api)
- Endex (CLAIMED, customer testimonial): source confabulations fell from 10% to 0%, and references per response rose 20%. Some press (Maginative) misreports the 20% as "reference accuracy". Thomson Reuters (CoCounsel) gave only a qualitative statement about minimizing hallucination risk and building trust, with no number. — [Anthropic announcement](https://anthropic.com/news/introducing-citations-api); [Maginative](https://www.maginative.com/article/anthropic-launches-citations-api-for-more-trustworthy-responses-from-claude/); [The Decoder](https://the-decoder.com/anthropic-adds-source-citations-to-claude-models/)
- OpenAI file_search (VERIFIED): each `file_citation` annotation includes `type`, `index`, `file_id` and `filename`. The docs show no quote text, character span, or page number. "The file search call will not return search results by default"; pass `include: ["file_search_call.results"]` to get them. Lowering `max_num_results` cuts tokens and latency but can lower answer quality. — [OpenAI file search guide](https://developers.openai.com/api/docs/guides/tools-file-search)
- Google Vertex grounding (VERIFIED): `groundingMetadata` has `groundingChunks` (retrievedContext uri/title) and grounding supports. Each support has a `segment` (`startIndex`, `endIndex` of the answer span), `supportChunkIndices`, and `confidenceScore`, defined as "A number from 0 to 1 that indicates how grounded the claim is in the provided set of grounding chunks". Confidence scores are "Not available for Gemini 2.5 and later." Metadata may be missing with "low-source relevance or incomplete information." — [Vertex AI grounding docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-vertex-ai-search)

### Inferences
- Vault's design (handles plus strict schema plus code-copied text) gives the same "valid pointer" guarantee as Anthropic Citations. It also keeps structured outputs, which Anthropic's feature cannot. That is a defensible differentiator for the write-up. Anthropic's custom-content mode (no extra chunking, block indices) is essentially the same idea as Vault's handles.
- OpenAI's native file_search annotations are too coarse for sentence-level highlighting (file-level only), so staying with application-managed handles on OpenAI is justified rather than switching to hosted vector stores.
- None of the vendors ships a usable support or confidence score on current models (Google dropped it for 2.5+). Calibrated abstention has to be built application-side.

### Gaps
- No independent third-party benchmark of Anthropic Citations accuracy was found. The 15%, 10%-to-0% and 20% figures are all vendor or customer reported.
- I did not verify whether OpenAI's file_search `index` is a character offset into the output text (the docs example shows `index: 992` without defining it). I also did not check OpenAI's url_citation (web search) fields.

## A2. Research on attribution quality (ALCE, citation precision/recall, LongCite, faithfulness of citations)

### Takeaway
Even strong models frequently produce citations that do not fully support their claims. ALCE found the best models lacked complete citation support about 50% of the time on ELI5. Separately, citations can be "correct" but unfaithful (post-rationalized) up to 57% of the time. Fine-grained sentence-level citation is learnable (LongCite), and the standard metric is NLI-judged citation recall/precision.

### Cited Findings
- ALCE (VERIFIED from abstract): "the first benchmark for Automatic LLMs' Citation Evaluation", with automatic metrics for fluency, correctness and citation quality that "correlate strongly with human judgments". On ELI5, "even the best models lack complete citation support 50% of the time." — [Gao et al., ALCE, arXiv 2305.14627](https://arxiv.org/abs/2305.14627)
- ALCE metric details (CLAIMED, from my background knowledge, not re-read this session): citation recall asks whether the concatenated cited passages entail each statement. Citation precision asks whether each individual citation is needed or supports the statement. Both are judged by an NLI model (TRUE/T5-11B). Confirm in the paper's §2 before quoting.
- LongCite (VERIFIED from abstract/listing): introduces LongBench-Cite. Current LLMs score "unsatisfactory" on it. The CoF (coarse-to-fine) pipeline builds LongCite-45k (44,600 instances, contexts up to 128k tokens). LongCite-8B/9B "achieve state-of-the-art citation quality, surpassing advanced proprietary models including GPT-4o", and SFT with citations "effectively reduces hallucinations." Published in Findings of ACL 2025. — [LongCite, arXiv 2409.02897](https://arxiv.org/abs/2409.02897)
- Correctness ≠ faithfulness (VERIFIED via abstract and repository record): a citation can be correct (the document supports the claim) yet unfaithful (the model did not actually rely on it, i.e. post-rationalization). In their experiments, up to 57% of citations lacked faithfulness. — [Wallat et al., arXiv 2412.18004](https://arxiv.org/abs/2412.18004); [TU Delft record](https://repository.tudelft.nl/record/uuid:bab4a12e-455b-49ea-bdf8-7f6a88478f60)

### Inferences
- Code-copied `cited_text` removes fabricated quotes but not wrong-span or unsupporting citations. The ALCE failure mode (about 50% incomplete support) is the residual risk Vault still faces. That is the argument for a post-hoc support check (A3) and for measuring citation precision/recall in `eval/`.
- Vault's eval should report ALCE-style citation recall and precision, judged per answer statement against the concatenated cited sentences. This is standard and comparable.
- Faithfulness (post-rationalization) can't be measured cheaply in production. Note it as a known limitation rather than claiming to solve it.

### Gaps
- Exact LongBench-Cite citation F1 numbers (LongCite vs GPT-4o) were not extracted. See the tables in arXiv 2409.02897 v3.
- No 2025-2026 measured rate of "wrong span cited" for current OpenAI GPT-6.x models was found.

## A3. Post-hoc verification (NLI / grounding checkers)

### Takeaway
Small dedicated checkers reach roughly GPT-4-level grounding accuracy at about 400x lower cost (MiniCheck), and HHEM-2.1-Open runs on CPU in under 1.5 s per 2k-token pair. Accuracy on realistic RAG benchmarks is moderate, though (about 64-75% balanced accuracy, and around 53% on adversarial FaithBench). Treat these checkers as a gating or flagging signal, not ground truth.

### Cited Findings
- MiniCheck (VERIFIED, EMNLP 2024): MiniCheck-FT5 (770M, Flan-T5) "outperforms all systems of comparable size and reaches GPT-4 accuracy" on LLM-AggreFact. LLM-AggreFact aggregates 10 datasets with human sentence-level labels. The model is trained on GPT-4-synthesized data. The paper reports GPT-4-level performance at about 400x lower cost. — [MiniCheck, arXiv 2404.10774](https://arxiv.org/abs/2404.10774)
- MiniCheck numbers (CLAIMED, secondary summary): 74.7% balanced accuracy, and about $0.24 vs $107 for GPT-4 on the 13K LLM-AggreFact test set. Explicit claim decomposition and decontextualization do not consistently help, and they add cost. — [Maxim summary](https://www.getmaxim.ai/blog/minicheck-llm-fact-check/); leaderboard at [llm-aggrefact.github.io](https://llm-aggrefact.github.io)
- HHEM-2.1-Open (CLAIMED, vendor model card/blog): latency on an Intel Xeon w7-3445 under 1.5 s for 2k combined premise+hypothesis length, and under 600MB of memory at fp32. RAGTruth-Summ balanced accuracy is 64.42% (vs 53.36% for HHEM-1.0 and 58.49% for GPT-3.5-Turbo zero-shot). RAGTruth-QA is 74.28% (secondary summary, not confirmed in the primary table). — [Vectara HHEM-2.1 blog](https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model); [HF model card](https://huggingface.co/vectara/hallucination_evaluation_model)
- HHEM caveats (CLAIMED): balanced accuracy drops to 62.58% on longer premises (TofuEval), and FaithBench (adversarial) gives about 52.6% balanced accuracy. — [Vectara commercial vs open blog](https://www.vectara.com/blog/hallucination-detection-commercial-vs-open-source-a-deep-dive)

### Inferences
- For Vault, the cheapest high-value check is per-statement entailment of each answer sentence against its cited sentences (short premise, so these checkers are at their best). Run it as a reason-coded flag (e.g., `CITATION_WEAK_SUPPORT`), not by asking the model to explain itself.
- Adding a local checker (MiniCheck/HHEM) means new model weights and CPU cost, and that would need justification under the "no new service" rule. The alternative is a `fast`-role LLM judge through `app.llm.gateway` on a sample, capped by `--max-usd`. Which one wins is not settled. Measure both on the eval set before deciding.
- A quote-overlap check is redundant in Vault, because `cited_text` is copied by code. The open question is only whether the span supports the claim.

### Gaps
- AlignScore and FactCC: no 2024-2026 numbers collected this session. FactCC is generally considered superseded by newer checkers, but that is background knowledge only, not verified here.
- No head-to-head of MiniCheck vs HHEM on the same RAG-QA set was found.

## A4. Abstention / "I don't know" and calibration

### Takeaway
Google's Sufficient Context paper (ICLR 2025) shows that models hallucinate rather than abstain when context is insufficient, and that larger models are overconfident. Combining a context-sufficiency signal with self-rated confidence (selective generation) improves accuracy on answered questions by only 2-10%. Abstention should be a code-owned gate using several signals, not a prompt instruction.

### Cited Findings
- Sufficient Context (VERIFIED from abstract): larger models (Gemini 1.5 Pro, GPT-4o, Claude 3.5) "excel at answering queries when the context is sufficient, but often output incorrect answers instead of abstaining when the context is not." Smaller models (Mistral 3, Gemma 2) hallucinate or abstain often even with sufficient context. Selective generation using sufficient-context information "improves the fraction of correct answers among times where the model responds by 2-10% for Gemini, GPT, and Gemma." — [Joren et al., arXiv 2411.06037](https://arxiv.org/abs/2411.06037)
- Autorater (CLAIMED, secondary): an LLM autorater classifies query-context pairs as sufficient or insufficient with about 93% accuracy. — [Google Research blog](https://www.research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/); [VentureBeat](https://venturebeat.com/ai/why-enterprise-rag-systems-fail-google-study-introduces-sufficient-context-solution)
- Extra context can raise model confidence and push toward hallucination rather than abstention (CLAIMED, from Google's blog summary). — [Google Research blog](https://www.research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/)
- Vendor-side scores are unavailable: Vertex confidence scores are not returned for Gemini 2.5+ (VERIFIED). — [Vertex grounding docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-vertex-ai-search)

### Inferences
- A reasonable Vault design is an abstention gate combining (a) a retrieval signal (reranker top-score and margin), (b) a typed "sufficient: yes/partial/no" decision from a cheap model, and (c) the citation-resolution outcome (no resolved handles means abstain). Code applies the decision and the explanation comes from a reason code. The threshold should be tuned through the convergence engine on the dev split only.
- The 2-10% gain is a realistic ceiling to quote. Don't promise more.
- "Partial" answers (answer the covered part, flag the uncovered part) are probably better UX than binary abstention, given that the paper notes context helps even when insufficient. This is my inference, not a paper claim.

### Gaps
- False-abstention rates (abstaining when the context was sufficient) were not extracted as numbers. They are in the paper's tables (arXiv 2411.06037 v3) and the GitHub repo (github.com/hljoren/sufficientcontext).
- No 2025-2026 calibration results for OpenAI GPT-6.x were found.

## B1. Parser comparison (Docling, PyMuPDF4LLM, Marker, MinerU, Unstructured, LlamaParse, pdfplumber, pypdf)

### Takeaway
On license and CPU cost, Docling (MIT) is the strongest general-purpose open parser: good tables (TEDS about 0.89 on one vendor benchmark) at about 0.4-1.6 pages/s on CPU without OCR. PyMuPDF is fastest but AGPL, with weak tables. Marker's code is now Apache-2.0, but its weights carry a revenue-capped RAIL-M license, and its accurate mode is very slow on CPU. MinerU has moved from AGPL to an Apache-2.0-plus-conditions license. Benchmarks conflict across versions and publishers, so none should be treated as definitive.

### Cited Findings
**Licenses (VERIFIED via GitHub API/license files, 2026-11-13):**
- Docling: MIT — [GitHub docling-project/docling](https://github.com/docling-project/docling)
- pdfplumber: MIT — [GitHub jsvine/pdfplumber](https://github.com/jsvine/pdfplumber)
- Unstructured (open-source library): Apache-2.0 — [GitHub Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured)
- PyMuPDF4LLM: AGPL-3.0, with Artifex commercial licensing. The pymupdf4llm package is now an empty shim: "The pymupdf-2.x package now contains the 4llm code that used to be in pymupdf4llm-1.x." — [GitHub pymupdf/pymupdf4llm](https://github.com/pymupdf/pymupdf4llm)
- Marker: code is "licensed under Apache 2.0 — free to use, including commercially. Our model weights use a modified AI Pubs Open Rail-M license (free for research, personal use, and startups under $5M funding/revenue)." Older benchmarks list Marker as GPL-3.0, which is now out of date. — [Marker README](https://github.com/datalab-to/marker)
- MinerU: the "MinerU Open Source License" is Apache 2.0 plus extra terms. A separate commercial license is required above 100M MAU or USD 20M monthly revenue, online services must attribute MinerU, and the license terminates automatically on non-compliance. Older sources list AGPL-3.0, which is outdated. — [MinerU LICENSE.md](https://github.com/opendatalab/MinerU)

**Docling technical report (VERIFIED, arXiv 2408.09869, 225 pages, OCR off):**
- Apple M3 Max, 4 threads: native backend 1.27 pages/s (6.20 GB peak), pypdfium backend 2.18 pages/s (2.56 GB). Intel Xeon E5-2690, 4 threads: native 0.60 pages/s (6.16 GB), pypdfium 0.94 pages/s (2.42 GB). With 16 threads the Xeon reaches 1.57 pages/s on pypdfium. TableFormer takes "2 to 6 seconds" per typical table on CPU. EasyOCR OCR is "upwards of 30 seconds per page" on CPU. — [Docling Technical Report](https://arxiv.org/html/2408.09869)

**OpenDataLoader benchmark (CLAIMED: published by OpenDataLoader, itself a competitor; hardware and dataset not stated on the page):**

| Engine | Overall | Reading order | Table TEDS | Heading | s/page |
|---|---|---|---|---|---|
| opendataloader [hybrid] | 0.907 | 0.934 | 0.928 | 0.821 | 0.463 |
| docling | 0.882 | 0.898 | 0.887 | 0.824 | 0.762 |
| marker | 0.861 | 0.890 | 0.808 | 0.796 | 53.9 |
| unstructured hi_res | 0.841 | 0.904 | 0.588 | 0.749 | 3.0 |
| mineru | 0.831 | 0.857 | 0.873 | 0.743 | 5.96 |
| pymupdf4llm | 0.732 | 0.885 | 0.401 | 0.412 | 0.091 |
| unstructured (fast) | 0.686 | 0.882 | 0.000 | 0.388 | 0.077 |
| markitdown | 0.589 | 0.844 | 0.273 | 0.000 | 0.114 |

pdfplumber, pypdf and LlamaParse are not in this table. — [OpenDataLoader benchmark](https://opendataloader.org/docs/benchmark)

**OmniDocBench (CLAIMED from search-result extraction of the paper and derived tables; versions are not comparable):**
- v1.0 overall edit distance (lower is better): MinerU 0.150, Marker 0.336, Docling 0.589. Table TEDS: MinerU 78.6, Marker 67.6, Docling 61.3. — [OmniDocBench, arXiv 2412.07626](https://arxiv.org/pdf/2412.07626)
- v1.5: Marker-1.8.2 scores 71.30 overall (text ED 0.206, table TEDS 57.88), and the MinerU pipeline scores 75.51 (text ED 0.209, TEDS 70.90). Independent reruns differ from the published numbers. — [OmniDocBench paper](https://arxiv.org/pdf/2412.07626); [community leaderboard evidence](https://github.com/AIwork4me/omnidocbench-amd-windows/blob/main/docs/benchmarks/leaderboard-evidence-2026-08-01.md)
- Conflict: OmniDocBench v1.0 ranks Docling well below Marker and MinerU, while OpenDataLoader's benchmark ranks Docling above both. The likely causes are the corpus (OmniDocBench is heavy on Chinese and diverse document types), version, and config differences. Treat both with caution.

**Marker's own claims (CLAIMED, vendor README):** "Marker fast" runs on CPU at 23.6 pages/s and scores 71.0, "beating docling (67.5) at 11-17x the speed". "Marker no_ocr" runs at 57.0 pages/s on CPU and scores 81-83% on born-digital multi-column suites. This contradicts the OpenDataLoader figure of 53.9 s/page, which probably measured Marker's accurate or default mode. — [Marker README](https://github.com/datalab-to/marker)

**Other CPU speed claims (CLAIMED):** a docling.org comparison reports Docling's default pipeline at 6.6 s/page vs Marker 1.10.2 at 90.7 s/page on CPU. Note that docling.org may not be the official IBM project site; the official repo is docling-project. — [docling.org/compare](https://docling.org/compare/)

**pdfplumber (VERIFIED):** char objects expose x0, top, fontname and the CTM `matrix`. It "Works best on machine-generated, rather than scanned, PDFs", does no OCR, and lists strong support for OCR'd tables as a non-feature. Table detection works from drawn or implied lines and their intersections (Nurminen thesis, Tabula-inspired). — [pdfplumber README](https://github.com/jsvine/pdfplumber)

### Inferences
- For Vault (born-digital PDFs, CPU, permissive license, character-exact evidence), keeping pdfplumber as the evidence source of truth is defensible. Docling (MIT) is the strongest candidate as an optional layout and table oracle (structure only) whose output gets reconciled back onto pdfplumber's glyph stream, never used as evidence text. That would keep invariant I1. PyMuPDF (AGPL) is a poor fit for a service, and Marker/MinerU carry commercial-threshold terms plus higher CPU cost.
- Vendor benchmarks disagree by large margins. The only trustworthy comparison for Vault is running candidates on `eval/datasets/vault_eval_v1` itself (with `make eval-extraction`), sampled, with the regenerate command recorded.

### Gaps
- LlamaParse: no 2025-2026 numbers, pricing, or provenance details collected (it is a hosted, non-local service, which may conflict with Vault's self-hosted design anyway).
- pdfplumber and pypdf appear in none of the benchmarks found, so their table and reading-order quality versus Docling is UNKNOWN on public data.
- Unstructured hi_res CPU memory figures are not collected.
- The OmniDocBench numbers came from a search-result extraction, not a direct read of the paper tables. Re-verify before quoting.

## B2. Which parsers preserve character-level or bbox provenance for exact highlighting

### Takeaway
Only low-level PDF libraries (pdfplumber/pdfminer, PyMuPDF, pypdfium2) expose per-character coordinates. Docling exposes item-level provenance (page number, bbox, charspan) but not per-glyph boxes, so exact sentence highlighting still needs a character-level layer underneath.

### Cited Findings
- pdfplumber exposes per-character objects with position (x0, top), font and transformation matrix (VERIFIED). — [pdfplumber README](https://github.com/jsvine/pdfplumber)
- Docling's document model carries provenance with `page_no`, `bbox: BoundingBox` and `charspan` (VERIFIED in docling-core source; I saw `charspan=(0, len(text))` constructions, i.e. spans relative to an item's text). — [docling-core document.py](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/document.py)
- Docling's PDF backend extracts text tokens with page coordinates, and the layout model predicts bounding boxes (VERIFIED from the technical report, though the report does not state what reaches the final JSON). — [Docling Technical Report](https://arxiv.org/html/2408.09869)
- Anthropic's PDF citations resolve only to page ranges (`page_location`), not bboxes (VERIFIED). — [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)

### Inferences
- Item-level bboxes (Docling) are enough for paragraph or table-cell highlight boxes but not for sentence-exact highlights inside a paragraph. Vault's glyph-mapped character stream (research 05) is the right layer for that, and none of the higher-level parsers replaces it.
- Whether PyMuPDF4LLM's markdown output carries per-span bboxes is unverified (`page_chunks` mode is believed to include page metadata). Moot given the AGPL license.

### Gaps
- Marker and MinerU provenance (JSON block bboxes) was not verified this session.
- Docling `charspan` semantics were inferred from source construction sites, not docs.

## B3. DOCX: python-docx vs Docling vs pandoc (tables, tracked changes)

### Takeaway
Pandoc is the only one of the three with documented, explicit tracked-changes handling (`--track-changes=accept|reject|all`). No evidence was found that python-docx models revisions, so Vault should handle `w:ins`/`w:del` explicitly at the XML level if tracked changes matter.

### Cited Findings
- Pandoc (VERIFIED): `--track-changes` "Specifies what to do with insertions, deletions, and comments produced by the MS Word 'Track Changes' feature." It takes `accept` (the default), `reject` or `all` (keeps everything, marked with spans), and "This option only affects the docx reader." — [Pandoc manual](https://pandoc.org/MANUAL.html)
- python-docx: grepping its HISTORY.rst for "revision", "track", "w:ins" and "w:del" found no tracked-changes or revision feature entries (VERIFIED absence in the changelog only, which is weak evidence). — [python-docx HISTORY](https://github.com/python-openxml/python-docx/blob/master/HISTORY.rst)

### Inferences
- UNVERIFIED (background knowledge): python-docx's `Paragraph.runs` and `.text` iterate only direct `w:r` children, so text inside `w:ins` wrappers can be silently dropped, and deleted text (`w:delText`) is ignored. Test this on a fixture with tracked changes before relying on it. If true, it is a silent evidence-loss bug for Vault's DOCX path, worth a negative test.
- Pandoc adds a Haskell binary dependency, so for Vault a small explicit `w:ins`/`w:del` handler on top of python-docx is probably the smaller diff. Docling's DOCX backend behavior on tracked changes is unknown.

### Gaps
- Docling DOCX backend: no verified information on tracked changes, merged table cells, or provenance for DOCX.
- No benchmark comparing DOCX table fidelity across python-docx, Docling and pandoc was found.
