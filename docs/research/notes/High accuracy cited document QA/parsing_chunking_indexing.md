# Document parsing, chunking and indexing for high-accuracy RAG (contracts, 10-K tables, scientific papers)

Context: Vault uses pdfplumber glyph-mapped extraction (only `original_text` is evidence), chunker@1 (~356-token median, token-based, some tables as own chunks), hybrid Postgres tsvector + text-embedding-3-small with RRF, identifier tokens indexed, optional contextual chunk headers. Known misses: QASPER and 10-K tables. Research budget: ~20 tool calls; several primary sources read in full (arXiv 2402.05131, Chroma report, arXiv 2604.12047 PDF, T2-RAGBench, DISRetrieval, Anthropic contextual retrieval). Confidence tags: VERIFIED = read in primary source; CLAIMED = secondary summary or vendor claim not checked against primary.

## Chunking: which strategy and size has the best evidence?

### Takeaway
No chunking scheme wins everywhere. The best-evidenced, cheap moves are (a) structure/element-aware boundaries (headings, tables as units) at roughly 250-512 tokens, (b) about 25% overlap in the token range we use, and (c) storing several granularities or a parent (page/section) to expand into after retrieval. Semantic chunking by embedding similarity is not worth its cost. Proposition chunking and late chunking give small or inconsistent gains. Recommendation for Vault: keep a token-bounded chunker but cut at heading and element boundaries (structure-aware), keep tables whole, add about 25% overlap, and add a page/section "parent" for small-to-big expansion at answer time. Don't adopt semantic, proposition or late chunking.

### Cited Findings
- **Element-type chunking on FinanceBench (VERIFIED, vendor-authored by Unstructured):** setup was 80 docs, 141 questions, `multi-qa-mpnet-base-dot-v1`, top-10, GPT-4 generator. Page-retrieval accuracy: Base 128 = 72.34%, Base 256 = 73.05%, Base 512 = 68.09%, Chipper element chunks (prefix + table description) = 67.38%. "Aggregation" (union of all granularities) gave Base 83.69% and Chipper 84.40%. Manual Q&A accuracy: Base 128 = 35.46%, Base 256 = 36.88%, Base 512 = 48.23%, Chipper keywords / prefix+table-description = 53.19%. Element chunking also produced fewer chunks than Base 256 (20,843 vs 32,051). — [arXiv 2402.05131v3](https://arxiv.org/html/2402.05131v3)
  - Caveat: the headline "84.4%" is the multi-granularity aggregation, not element chunking alone. Element chunking alone scored *lower* on page retrieval (67.38%) than 256-token chunks, but *higher* on end Q&A (53.19% vs 36.88%). The embedder is weak (mpnet). Elements under 2,048 chars were merged with the next element. — [Moonlight review](https://www.themoonlight.io/review/financial-report-chunking-for-effective-retrieval-augmented-generation)
- **Chroma chunking study (VERIFIED, vendor research, synthetic 5-corpus set, 472 queries, n=5 retrieved, text-embedding-3-large):** recall / IoU by setting:
  - Recursive 800 tokens, 400 overlap: 85.4 / 1.5. TokenText 800/400 (the old OpenAI Assistants default): 87.9 / 1.4.
  - Recursive 400/0: 89.5 / 3.6. Recursive 200/0: 88.1 / 6.9.
  - Kamradt semantic: 83.6 / 1.5. ClusterSemantic 400: 91.3 / 4.5. LLM chunker (GPT-4o): 91.9 / 3.9.
  - Overlap raised recall in some settings but lowered IoU. The authors call the results trends, not definitive rankings. — [Chroma Research](https://www.trychroma.com/research/evaluating-chunking)
- **Vectara semantic-chunking study (CLAIMED from abstract/blog):** "benefits ... are inconsistent and often insufficient to justify the computational cost". Fixed-size chunking was competitive, and embedding quality mattered more than the chunker. Vectara's platform uses fixed-size chunking. — [arXiv 2410.13070](https://arxiv.org/html/2410.13070v1); [Vectara blog](https://www.vectara.com/blog/is-semantic-chunking-worth-the-computational-cost)
- **NVIDIA chunking study (CLAIMED via search snippets):** page-level chunking had the highest average end-to-end accuracy, 0.648 (std 0.107), across FinanceBench, Earnings, KG-RAG, RAGBattlePacket and DigitalCorpora767. Among token sizes, FinanceBench did best at 1,024 tokens and Earnings at 512. A secondary blog reports 15% overlap as best on FinanceBench; I did not verify that. — [NVIDIA dev blog](https://developer.nvidia.com/blog/?p=102394); [PremAI summary](https://blog.premai.io/rag-chunking-strategies-the-2026-benchmark-guide)
- **El Bachyr et al. 2026, PDF parsing × chunking on FinanceBench + TableQuest (VERIFIED, read PDF):** setup was 512-token chunks with page-level retrieval.
  - Overlap with the neural chunker raised MRR from 0.529 (0%) to 0.658 (25%) on FinanceBench, and from 0.735 to 0.833 on TableQuest. 50% overlap scored slightly worse (0.647 / 0.832).
  - Chunkers at 25% overlap on FinanceBench: token MRR 0.647, recursive 0.640, SDPM (semantic double-pass) 0.600, neural best at 0.658. The neural chunker beat sentence chunking by only +0.7 MRR points. At 0% overlap, semantic chunkers did worst (semantic best 0.568, SDPM 0.570), while pdfminer + recursive scored 0.655.
  - The authors' RQ3 answer: neural with 25% overlap is best, "with sentence as a close, lower-cost fallback". — [arXiv 2604.12047](https://arxiv.org/abs/2604.12047)
- **Late chunking (Jina; CLAIMED via summaries):** average relative nDCG@10 gain of +2.70% to +3.63% on BEIR. SciFact went from 64.20 to 66.10. There was no change on short-text sets (Quora). A 2026 taxonomy paper found contextualized/late chunking degrades in-document retrieval in its configurations (Nomic −53%, Jina-v3 −30%). — [arXiv 2409.04701](https://arxiv.org/html/2409.04701v3); [Jina GitHub](https://github.com/jina-ai/late-chunking); [Moonlight review of taxonomy paper](https://www.themoonlight.io/review/beyond-chunk-then-embed-a-comprehensive-taxonomy-and-evaluation-of-document-chunking-strategies-for-information-retrieval)
- **Proposition chunking (CLAIMED):** in the 2026 taxonomy paper, Jina-v3 proposition+contextualized averaged 0.4785 in-corpus nDCG@10, against paragraph 0.4949 and LumberChunker 0.4913. It "usually does not become the best method in absolute terms". — [Moonlight review](https://www.themoonlight.io/review/beyond-chunk-then-embed-a-comprehensive-taxonomy-and-evaluation-of-document-chunking-strategies-for-information-retrieval)

### Inferences
- Vault's ~356-token median is inside the band that the Chroma, Unstructured and El Bachyr studies find competitive (200-512 tokens). Chunk size alone is unlikely to explain our misses.
- Two cheap levers have measured effects: overlap (+0.13 MRR in El Bachyr, though at 512 tokens with a weaker chunker) and multi-granularity / parent expansion (+10-17 points page accuracy in 2402.05131 aggregation). Overlap duplicates evidence, so citation dedup must operate at sentence handles, which Vault already does.
- With glyph-mapped `original_text`, any chunker must cut on character offsets of the existing stream. Structure-aware boundaries (pdfplumber font-size/bold heading detection, table bboxes) fit that invariant. LLM or proposition chunkers rewrite text and do not.

### Gaps
- I did not find the original Dense X (Chen et al. 2023) recall numbers or a controlled parent-child (small-to-big) ablation with numbers outside 2402.05131's aggregation.
- I could not read NVIDIA's primary post. Its numbers are from snippets.

## Tables (10-K): what representation retrieves and answers best?

### Takeaway
The best measured gains for financial tables come from three things: keeping a table as one unit with its title/caption and column headers, adding a short LLM "table description/prefix" to the *index text* (never the evidence), and hybrid lexical + dense retrieval. Markdown is the de facto format in benchmarks. Hierarchy-preserving representations beat flat HTML by about 6 points (TabRAG). Summarization *replacing* table text hurts. Recommendation for Vault: one chunk per table (split by row groups only when oversized, repeating the header row in each piece). Index text = section path + table title + header row + a generated 1-2 sentence description listing the line items and periods. Evidence stays the glyph-mapped cell text. Keep BM25 strong for numbers and line-item names.

### Cited Findings
- **T2-RAGBench (2025; VERIFIED):** 32,908 QA pairs over FinQA, ConvFinQA, TAT-DQA and VQAonBD. Contexts are in markdown. With E5-large-instruct, Llama-3.3-70B and top-3:
  - Number Match / MRR@3: Base RAG 37.2 / 36.9, Hybrid BM25 41.3 / 37.8 (best), cross-encoder reranker (ms-marco-MiniLM) 31.8 / 30.3, HyDE 34.0 / 32.0, Summarization 18.8 / 36.5, SumContext (retrieve on summary, generate on full) 37.4 / 36.5. Oracle context scored 72.3 Number Match.
  - Embedder ablation, R@5: text-embedding-3-large 56.1, Gemini text-embedding-004 52.8, E5-instruct 53.3.
  - The authors attribute the reranker's drop to it not being trained on text+table data. — [arXiv 2506.12071](https://arxiv.org/html/2506.12071v1)
- **El Bachyr 2026 (VERIFIED):**
  - On table-focused TableQuest, ColBERT late-interaction scored MRR 0.844 against E5 0.751 and BM25 0.677. On narrative FinanceBench, E5 scored MRR 0.700 against ColBERT 0.610 and BM25 0.210.
  - For tables, pdfplumber had the top parser MRR (0.807). pdfplumber + sentence chunking led at 0% overlap.
  - pdfminer and pdfplumber were the most robust parsers across chunkers. — [arXiv 2604.12047](https://arxiv.org/abs/2604.12047)
- **2402.05131 (VERIFIED):** "Prefix & Table Description" Chipper chunks gave the highest ROUGE among the single-granularity runs (0.514) and tied for the best manual Q&A (53.19%). The headline caveats are in the chunking section. — [arXiv 2402.05131v3](https://arxiv.org/html/2402.05131v3)
- **TabRAG (NeurIPS 2025; CLAIMED via summary):** a hierarchical structured table representation beat flat HTML by about 6.4 points in ablation. TAT-QA is among its datasets. — [papernotes summary](https://en.papernotes.org/NeurIPS2025/segmentation/tabrag_improving_tabular_document_question_answering_for_retrieval_augmented_gen/); [emergentmind](https://www.emergentmind.com/topics/tabrag)
- **Markdown/HTML vs plain text (CLAIMED, thesis on WikiTableQuestions):** markdown or HTML improve LLM table QA over plain text. — [Freiburg thesis 2026](https://ad-publications.cs.uni-freiburg.de/theses/Master_Shantini_Marlarvizhi_2026.pdf)

### Inferences
- The T2-RAGBench result (hybrid best, summaries-as-replacement hurt, generic rerankers hurt) maps directly onto Vault. Keep the tsvector leg weighted for table chunks. Measure our fastembed cross-encoder on table queries before trusting it there. Use descriptions only as additive index text, which also satisfies "prefixes and summaries are never cited".
- ColBERT's lead on tables suggests token-level matching of line-item names and numbers matters. Vault's identifier-token indexing plus BM25 is the cheap proxy for it. Adding multi-vector retrieval would be a new service and needs justification.
- Because answering is evidence-sentence based, a table chunk should expose row-level "sentences" (row label + header + value), so citations resolve to a row rather than the whole table. That is a design inference, not measured.

### Gaps
- I found no controlled markdown vs HTML vs row-serialization comparison on TAT-QA, FinQA or FinanceBench for *retrieval*.
- DocFinQA numbers were not found in this pass.

## Scientific papers (QASPER): does section-aware indexing help?

### Takeaway
Measured gains from structure-aware or hierarchical retrieval on QASPER are modest: about +1 to +4.6 F1 points over flat chunking. Absolute numbers vary widely by metric, so only within-paper deltas are meaningful. Recommendation for Vault: section-path headers in index text (title > section > subsection), abstract chunk always eligible, figure/table captions as their own chunks. Retrieve at sentence/small chunk and expand to the section neighbourhood.

### Cited Findings
- **DISRetrieval (arXiv 2506.06313; VERIFIED):** setup was QASPER, 400-word context, top-5, UnifiedQA-3B reader.
  - F1-Match with OpenAI text-embedding-3-large: flat-chunk 39.03, flat-sentence 40.06, RAPTOR 39.53, DISRetrieval (discourse tree + LLM node summaries) 40.74. With SBERT: 36.13 against 40.65.
  - Retrieval token recall (OpenAI): flat-chunk 73.38, DISRetrieval 75.95.
  - Golden evidence reaches 50.71 F1 with about 129 words, against 48.81 for the full document (4,170 words). Retrieval quality is the bottleneck.
  - On QuALITY, summary-based retrieval underperformed leaf retrieval. — [arXiv 2506.06313](https://arxiv.org/html/2506.06313v4)
- **Hierarchical segmentation chunking (CLAIMED):** QASPER F1 24.67 with 1,024-token segment-cluster chunks, against 20.33 for a 512-token base. — [arXiv 2507.09935](https://arxiv.org/html/2507.09935v1)
- **SF-RAG (CLAIMED):** a structure-fidelity, section-aware method scored *lower* evidence F1 than naive RAG (51.67 vs 54.28) but gave more compact, section-aligned context. — [arXiv 2602.13647](https://arxiv.org/pdf/2602.13647)
- **HiChunk (CLAIMED):** QASPER has dense evidence, and better chunking yields higher evidence recall and answer quality. — [arXiv 2509.11552](https://arxiv.org/pdf/2509.11552)
- **Contextual retrieval (VERIFIED):** Anthropic's test domains include arXiv / science papers. The gains are reported in the enrichment section below. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)

### Inferences
- With a strong embedder (OpenAI), the gap between flat-sentence and fancy hierarchy is under 1 F1 point (40.06 vs 40.74). The sentence-level granularity is doing most of the work. For Vault, small units plus section headers in the index text is the cheap 80%. LLM summary trees are not justified.
- QASPER questions often need "the dataset/baseline used" from Experiments sections. Section-path text in BM25 ("Experiments > Datasets") is a plausible fix for our misses. This is UNVERIFIED and needs an eval.

### Gaps
- I found no clean ablation isolating "section headers in index text" or "caption chunks" on QASPER with numbers.

## Embeddings: is text-embedding-3-small the bottleneck?

### Takeaway
Domain benchmarks show text-embedding-3-small trails top commercial models by a material margin: about 12 points on legal MLEB and about 6 points nDCG@10 on FinMTEB against voyage-3-large. The swap is a measurable but costly re-index. Recommendation for Vault: keep 3-small for now and run a paired retrieval eval of text-embedding-3-large (same provider and gateway, no new dependency) on train-split QASPER and 10-K queries. Consider Voyage only if the design allows a new provider.

### Cited Findings
- **MLEB legal (Oct 2025; CLAIMED from abstract/excerpt):** task average for text-embedding-3-small 73.88, BGE-M3 69.44, Voyage 3 Large 85.71, Voyage 3.5 84.07. — [arXiv 2510.19365](https://arxiv.org/html/2510.19365v1)
- **FinMTEB (EMNLP 2025; CLAIMED, column unclear):** voyage-3-large 0.6765 against text-embedding-3-small 0.6136 on an averaged retrieval column. — [FinMTEB](https://aclanthology.org/2025.emnlp-main.179.pdf)
- **T2-RAGBench (VERIFIED):** text-embedding-3-large had the best R@5 (56.1) among the 5 tested on financial text+table retrieval. — [arXiv 2506.12071](https://arxiv.org/html/2506.12071v1)
- **Vendor-adjacent finding (VERIFIED, Anthropic):** "Gemini and Voyage" were the most effective embedders in Anthropic's tests. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)

### Inferences
- The embedder probably matters more than the chunker (Vectara's conclusion, plus the Chroma MiniLM vs 3-large gap of about 10 recall points).

### Gaps
- I did not verify query-instruction prefixes, Matryoshka dimension truncation numbers, or optimal hybrid weights. RRF k sensitivity was not researched.

## Index-time enrichment: contextual headers, doc2query, metadata

### Takeaway
Contextual retrieval (an LLM-generated 50-100 token context prepended to each chunk for both the embedding and BM25 indexes) has the strongest measured gain: −35% failed retrievals from embeddings alone, −49% with BM25, −67% with a reranker. Recommendation for Vault: turn on contextual chunk headers for both the dense and the tsvector index text, generated per document with prompt caching. Verify the gain on our eval via the convergence engine. Doc2query/HyDE-style generated text is lower priority; HyDE hurt on financial tables.

### Cited Findings
- **Anthropic contextual retrieval (VERIFIED, vendor):**
  - Metric: 1 − recall@20, using Gemini Text 004.
  - Failure rate: baseline 5.7%, contextual embeddings 3.7% (−35%), plus contextual BM25 2.9% (−49%), plus Cohere rerank of the top 150 to 20 1.9% (−67%).
  - Context generation: Claude 3 Haiku, 50-100 tokens per chunk, about $1.02 per million document tokens with prompt caching. Top-20 beat top-10 and top-5.
  - Domains: codebases, fiction, arXiv / science papers. Some secondary sources also list financial documents; I did not confirm that on the primary page. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval); [Maginative](https://www.maginative.com/article/anthropics-contextual-retrieval-technique-enhances-rag-accuracy-by-67)
- **Keywords as chunk metadata (VERIFIED):** in 2402.05131, "Keywords Chipper" page accuracy was 46.10%, against 67.38% for "Prefix & Table Description". Keyword lists alone did worst. — [arXiv 2402.05131v3](https://arxiv.org/html/2402.05131v3)
- **HyDE (VERIFIED):** lowered MRR@3 from 36.9 to 32.0 on T2-RAGBench. — [arXiv 2506.12071](https://arxiv.org/html/2506.12071v1)

### Inferences
- Prose descriptive prefixes beat bare keyword lists. The prefix should name the document, the period/entity and the section, which is what a 10-K table chunk lacks ("Total revenue 2023" with no company or segment).

### Gaps
- I found no 2024-2026 doc2query-per-chunk study on financial or legal corpora with numbers.

## Postgres lexical: tsvector vs real BM25 (pg_search / VectorChord-bm25)

### Takeaway
`ts_rank` lacks IDF and length normalization, unlike BM25. Vendor and blog evidence suggests a large quality gap, but I found no rigorous BEIR comparison. Recommendation for Vault: keep tsvector for filtering, but run a paired eval of a BM25 extension (pg_search or VectorChord-bm25) on the lexical leg before adopting it. It is a new dependency and needs a design note.

### Cited Findings
- `ts_rank` uses term frequency without corpus-level document frequency; pg_search implements BM25 with TF, length normalization and IDF (vendor doc). — [ParadeDB](https://www.paradedb.com/learn/search-in-postgresql/bm25)
- **Blog benchmark (CLAIMED, suspicious):** nDCG@10 for ts_rank 0.07, BM25 (pg_textsearch, not pg_search) 0.69, pgvector 0.66, hybrid 0.70. A 0.07 suggests a misconfigured query (for example AND-semantics `plainto_tsquery`), so don't rely on the magnitude. — [Pedro Alonso](https://www.pedroalonso.net/blog/postgres-bm25-search/)
- **ParadeDB speed claim (vendor):** 20× faster than tsquery/ts_rank over 1M rows. This measures speed, not relevance. — [ParadeDB blog](https://www.paradedb.com/blog/introducing-search)
- **Neutral-ish comparison:** ts_rank_cd returns different rows from BM25 engines, and a BM25 index serves only some query shapes. — [SereneDB](https://serenedb.com/blog/searchbench-postgres)
- In financial page retrieval, BM25 alone was weak on narrative FinanceBench (MRR 0.210) but decent on tables (0.677). — [arXiv 2604.12047](https://arxiv.org/abs/2604.12047)

### Inferences
- Under RRF only rank order matters. tsvector's weaker ranking affects us through ordering within the lexical list. The OR-vs-AND query construction (`websearch_to_tsquery` vs OR of lexemes) may matter as much as the BM25 formula. This is UNVERIFIED and cheap to test.

### Gaps
- I found no VectorChord-bm25 quality numbers and no BEIR nDCG for pg_search against ts_rank under identical setup.
