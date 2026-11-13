# Retrieval best practices for a personal document vault (2025-2026), with quantitative evidence

Scope: retrieval design for tens to thousands of PDFs/DOCX (contracts, financial reports, papers, policies), OpenAI models, Postgres/pgvector, laptop-class CPU for any local model. Our measured state, for comparison: hybrid (pgvector + Postgres full-text) fused with RRF gives recall@8 of about 0.74 on a 122-item golden set. A MiniLM-L6 cross-encoder on CPU took 1.3-2.6 s for 40 passages and mostly timed out. End-to-end answers take about 6 s.

Confidence tags: VERIFIED means I read it on the primary source page. SECONDARY means it came from a summary or search snippet and was not checked against the primary source. VENDOR means the vendor reported it about its own product.

---

## 1. Anthropic "Contextual Retrieval": reported failure-rate reductions, cost, replications

### Takeaway
Anthropic reports that the top-20 retrieval failure rate falls from 5.7% to 3.7% with contextual embeddings (-35%), to 2.9% when contextual BM25 is added (-49%), and to 1.9% when reranking is added on top (-67%). The one-time cost is $1.02 per million document tokens using Claude 3 Haiku with prompt caching. Independent replications are thin, and the only partial one is vendor-run. That replication shows the biggest gain on the keyword (BM25) side.

### Cited Findings
- VERIFIED, VENDOR. The metric is 1 - recall@20. Baseline failure is 5.7%. Contextual Embeddings give 3.7% (35% reduction). Contextual Embeddings + Contextual BM25 give 2.9% (49% reduction). Adding reranking gives 1.9% (67% reduction). — [Anthropic, Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)
- VERIFIED. Cost is $1.02 per million document tokens, one time, with prompt caching. The assumptions are 800-token chunks, 8k-token documents, 50-token instructions and about 100 tokens of generated context per chunk. The contextualizer was Claude 3 Haiku. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- VERIFIED. The reranker was Cohere. The pipeline retrieves the top 150 chunks and reranks them to the top 20. Anthropic tested top-K of 5, 10 and 20, and 20 performed best. Gemini Text 004 and Voyage embeddings did best. The datasets were codebases, fiction, ArXiv papers and science papers. There were no contracts or financial filings. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- VERIFIED. Anthropic's guidance is that a knowledge base under 200,000 tokens (about 500 pages) can skip RAG and go entirely in the prompt. It also says prompt caching cuts latency by more than 2x and cost by up to 90%. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- SECONDARY, VENDOR. Denser AI reran the experiment on Anthropic's dataset. Keyword-search recall@20 rose from 70.48 to 89.26 with contextual augmentation. Vector-search recall@20 rose from 90.06 to 94.48. A reranker raised it to 96.10. — [Denser AI](https://denser.ai/blog/compare-open-source-paid-models-anthropic-dataset/)
- SECONDARY. Merola & Singh compared late chunking with contextual retrieval on a 20% subset of NFCorpus, because of GPU memory limits. They found contextual retrieval with rank fusion generally better than late chunking on NDCG, MAP and F1. — [alphaxiv overview of 2504.19754](https://alphaxiv.org/overview/2504.19754v1)
- SECONDARY. Unstructured reports its own contextual-chunking gains on a 5,563-question SEC-filing set using 1 - recall@20. I could not retrieve the numbers. — [Unstructured blog](https://unstructured.io/blog/contextual-chunking-in-unstructured-platform-boost-your-rag-retrieval-accuracy)

### Inferences
- Anthropic's baseline is already strong: 94.3% recall@20 with a top-150 candidate pool. Our recall@8 of 0.74 is a different K and a different corpus, so the relative reductions should not be expected to transfer as-is. The direction is consistent across Anthropic and Denser, though. Denser's +18.8-point keyword gain suggests that contextual text in the full-text index may help more than contextual text in the embedding.
- Rough re-costing with this project's fast-role model, using UNVERIFIED third-party pricing from §6 (gpt-6-luna at $0.10/M input, $0.01/M cached, $0.50/M output, 1.25x for a 30-minute cache write), under Anthropic's assumptions:
  - per 1M document tokens: about 1,250 chunk calls
  - cached document reads: about 10M tokens, roughly $0.10
  - cache writes: about $0.125
  - uncached chunk and instruction tokens: about 1.06M, roughly $0.11
  - output: 125k tokens, roughly $0.06
  - total: about $0.40 per 1M document tokens
  
  A vault of 1,000 documents at 20k tokens each (20M tokens) would cost about $8 one time. This is my arithmetic, not a sourced figure.
- A project rule applies here: only `chunks.original_text` is evidence. The generated context must go only into the embedding and FTS input, never into cited text. That is compatible with Anthropic's method, which prepends context for indexing only.

### Gaps
- I found no peer-reviewed, third-party replication of the exact 35/49/67% figures, and none on contracts or 10-Ks.
- Anthropic did not publish per-dataset tables. The numbers are averaged across embedding models.

---

## 2. Hybrid BM25 + dense with RRF vs dense alone; Postgres FTS vs true BM25

### Takeaway
Hybrid fusion reliably matches or beats either retriever alone. Over a strong neural retriever the gain is small (about 1-3% nDCG@10 on BEIR), and over BM25 it is large (about 18-20%). Tuned score-based fusion beats RRF by a few percent but needs per-dataset tuning. Postgres `ts_rank` is not BM25: it has no IDF, no term-frequency saturation, and no length normalization by default. True BM25 is available as the extensions pg_textsearch, ParadeDB pg_search and VectorChord-bm25, but all their comparative benchmarks are vendor-run.

### Cited Findings
- VENDOR. On BEIR, RRF of BM25 + ELSER (learned sparse) raised average nDCG@10 by 1.4% over ELSER alone and 18% over BM25 alone. Picking the best method per dataset gave 3% and 20%. Fused results were better than or similar to BM25 on every dataset. A weighted linear combination beat RRF on average, but its optimal weight "varies significantly" across datasets and models. — [Elastic Search Labs](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)
- SECONDARY. A third-party test on six BEIR datasets found RRF 3.86% lower in nDCG@10 than score-normalized hybrid on all six, for under 2% latency gain. — [BigData Boutique](https://bigdataboutique.com/blog/hybrid-search-fusion-methods-compared)
- VERIFIED, VENDOR. Anthropic: adding contextual BM25 to contextual embeddings cut top-20 failures from 3.7% to 2.9%. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- SECONDARY. Microsoft docs on Postgres FTS: `ts_rank` has "no term saturation, no length normalization, no native IDF". BM25 has all three. — [Microsoft Learn, HorizonDB full-text search](https://learn.microsoft.com/azure/horizondb/ai/full-text-search)
- VERIFIED (qualitative). An independent write-up notes that Postgres applies no length normalization by default. The author believes built-in search results "may be somewhat worse" than BM25, but reports no relevance measurements. — [emschwartz.me](https://emschwartz.me/comparing-full-text-search-algorithms-bm25-tf-idf-and-postgres/)
- SECONDARY, VENDOR. ParadeDB's benchmark explains that `ts_rank` scores every matching row before `ORDER BY ... LIMIT`, which is a latency problem. — [ParadeDB blog](https://www.paradedb.com/blog/benchmarker-iteration)
- SECONDARY, VENDOR. Tiger Data's pg_textsearch 1.0 (March 2026) claims 2-6x faster queries and 8.7x throughput with "better relevance", benchmarked against ParadeDB at 138M documents. — [Tiger Data](https://tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres)
- SECONDARY, VENDOR. pg_fts claims up to about 40x faster than `ts_rank` on common-term top-k, and says it trails VectorChord-bm25 and pg_textsearch on latency. — [PGXN pg_fts](https://pgxn.org/dist/pg_fts/)

### Inferences
- In our setup, the full-text leg is weaker than a true BM25 leg would be (no IDF, no saturation). Two cheap steps are worth trying before adding an extension: a normalization flag (`ts_rank_cd(..., 32)` or the length-normalization option) and weighting the title and section headings. If an extension is adopted, CLAUDE.md requires stating why, since it is a new dependency.
- The RRF-vs-weighted result means a tuned weighted fusion could add a few points, but it must go through the convergence engine and be tuned on the train split only.

### Gaps
- I found no independent nDCG comparison of `ts_rank` vs BM25 on a standard benchmark, and no BEIR table of dense alone vs dense + BM25 RRF for OpenAI embeddings specifically.

---

## 3. Rerankers: quality and CPU latency for about 40 passages

### Takeaway
On BEIR (rerank of BM25 top candidates), modern rerankers cluster at about 54-57 nDCG@10. These are vendor numbers: mxbai-rerank-large-v2 57.49, mxbai-base-v2 55.57, Cohere 3.5 55.39, jina-v2 54.35, bge-v2-m3 53.94. I found no controlled CPU benchmark. GPU latencies are already 0.3-3 s per query, so most of these are too slow for a laptop CPU at 40 passages. On TREC DL19, the MiniLM-L6 cross-encoder (74.30 nDCG@10) is almost as good as L12 (74.31). L4 (73.04) and L2 (71.01) are 1.4x-2.3x faster. LLM listwise reranking is accurate (RankGPT-4 at about 75.6 on DL19), but latency is a full LLM call.

### Cited Findings
- VERIFIED, VENDOR (Mixedbread). BEIR English nDCG@10, BM25 first stage:

  | Model | nDCG@10 |
  |---|---|
  | mxbai-rerank-large-v2 (1.5B) | 57.49 |
  | mxbai-rerank-base-v2 (0.5B) | 55.57 |
  | cohere-rerank-3.5 | 55.39 |
  | bge-reranker-v2-gemma (2.5B) | 55.38 |
  | voyage-rerank-2 | 54.54 |
  | jina-reranker-v2-base-multilingual | 54.35 |
  | bge-reranker-v2-m3 (568M) | 53.94 |

  — [Mixedbread, mxbai-rerank-v2](https://mixedbread.com/blog/mxbai-rerank-v2)
- VERIFIED, VENDOR. Latency on an A100 80GB GPU (NFCorpus, seconds per query): mxbai-xsmall-v1 0.32, base-v2 0.67, large-v2 0.89, bge-v2-m3 3.05, bge-v2-gemma 7.20. The v2 models are Apache 2.0. — [Mixedbread](https://mixedbread.com/blog/mxbai-rerank-v2)
- VERIFIED. Sentence-Transformers MS MARCO cross-encoders. The page does not state the hardware for the docs/sec figures:

  | Model | TREC DL19 nDCG@10 | MS MARCO dev MRR@10 | docs/sec |
  |---|---|---|---|
  | TinyBERT-L2 | 69.84 | 32.56 | 9000 |
  | MiniLM-L2 | 71.01 | 34.85 | 4100 |
  | MiniLM-L4 | 73.04 | 37.70 | 2500 |
  | MiniLM-L6 | 74.30 | 39.01 | 1800 |
  | MiniLM-L12 | 74.31 | 39.02 | 960 |

  — [SBERT pretrained cross-encoders](https://www.sbert.net/docs/cross_encoder/pretrained_models.html)
- SECONDARY. The Hindsight reranker leaderboard ran local models on CPU in Docker. jina-reranker-v2-multilingual averaged about 1.06 s per call and FlashRank MiniLM-L12 about 3.16 s per call. Candidate count was not captured, and the page returned 404 when fetched. — [search snippet of benchmarks.hindsight.vectorize.io](https://benchmarks.hindsight.vectorize.io/leaderboard/reranker)
- SECONDARY. License conflict: jina-reranker-v2-base-multilingual is reported both as Apache 2.0 and as cc-by-nc-4.0 (non-commercial). Check the model card. — [ZeroEntropy](https://www.zeroentropy.dev/articles/open-source-alternatives-to-cohere-rerank)
- SECONDARY. answerai-colbert-small-v1 (33M params) has a BEIR average of 53.79, above bge-base-en (53.25) per its model card. CPU claims are only qualitative ("milliseconds on CPU" for search). — [HF model card](https://huggingface.co/answerdotai/answerai-colbert-small-v1); [Answer.AI](https://www.answer.ai/posts/2024-08-13-small-but-mighty-colbert.html)
- SECONDARY. LLM listwise reranking of BM25 top-100. On TREC DL19, RankGPT-3.5 scores 65.80 and RankGPT-4 75.59, vs BM25 at 50.58. The BEIR averages are 49.37 and 53.68. Reproducing all the GPT ranking results cost $100-200 at 2023 prices. — [arXiv 2406.14848](https://arxiv.org/pdf/2406.14848); [DeAR, arXiv 2508.16998](https://arxiv.org/pdf/2508.16998); [RankZephyr, arXiv 2312.02724](https://arxiv.org/pdf/2312.02724)
- VERIFIED, VENDOR. Anthropic's reranking stage (Cohere, 150 to 20) took failures from 2.9% to 1.9%. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)

### Inferences
- Our MiniLM-L6 measurement of 1.3-2.6 s for 40 passages works out to roughly 15-30 pairs per second. That is far below MiniLM-class throughput, which suggests the cost is in sequence length (long chunks truncated at 512 tokens), threading in the container, or lack of ONNX/int8, not in the model choice. This is my inference and was not measured here. Three cheap experiments:
  - cap pair length at about 256 tokens (query plus the highest-scoring sentence window)
  - use ONNX int8
  - try MiniLM-L4 or L2, which give up 1.3-3.3 DL19 points
- Larger rerankers (bge-v2-m3, mxbai-v2, jina-v2) take 0.7-3 s per query even on an A100. They are not viable on a laptop CPU within a sub-second budget.
- Two plausible alternatives:
  - Cohere Rerank 3.5 via API (55.39 BEIR, vendor-reported). It adds a new provider, against the OpenAI-only design.
  - Listwise reranking with the fast-role OpenAI model. About 40 passages × about 200 tokens is about 8k input tokens, roughly $0.001 per query at the unverified Luna price in §6. The cost is one extra LLM round trip on top of a 6 s answer, but it can also return typed relevance decisions.
- A third option is to drop reranking and spend the budget on contextual BM25. Per Anthropic, that is the bigger step: 3.7% to 2.9% failures, vs 2.9% to 1.9% for reranking.

### Gaps
- I found no apples-to-apples CPU latency benchmark (same CPU, same passage length, 40 candidates) for Cohere 3.5, bge-v2-m3, jina-v2, mxbai, FlashRank or ColBERT.
- I found no published quality numbers for listwise reranking with small, cheap 2025-26 models (Luna-class).

---

## 4. Query transformation: HyDE, multi-query, step-back, decomposition

### Takeaway
HyDE's published gains are large only over a weak, unsupervised embedder: Contriever goes from 44.5 to 61.3 nDCG@10 on DL19. Even then HyDE roughly ties a supervised retriever (62.1) and trails it on DL20 (57.9 vs 63.2). Decomposition helps multi-hop questions and can hurt simple lookups. Every method adds at least one LLM call of latency. Rigorous 2025 numbers are scarce, and most figures are practitioner blogs.

### Cited Findings
- SECONDARY. HyDE paper on TREC DL19/DL20 (nDCG@10):
  - Contriever alone: 44.5 / 42.1
  - BM25: 50.6 / 48.0
  - HyDE (InstructGPT + Contriever): 61.3 / 57.9
  - fine-tuned Contriever-ft: 62.1 / 63.2
  
  — [finisky summary of HyDE](https://finisky.github.io/en/hyde-paper-summary/); [Liner review](https://liner.com/review/precise-zeroshot-dense-retrieval-without-relevance-labels)
- SECONDARY. ReDE-RF (arXiv 2410.21242), measured on one A100, is on average 3.8x faster than HyDE end to end. The authors call per-query hypothetical-document generation "inefficient". — [arXiv 2410.21242](https://arxiv.org/pdf/2410.21242)
- SECONDARY (single-author blog, anecdotal). Decomposition gave about +11 recall points on multi-hop queries and about -5 on fact lookups. Multi-query with 4 rewrites gave recall@10 of 0.74 vs a 0.71 baseline on fact lookup. — [DEV Community](https://dev.to/gabrielanhaia/hyde-multi-query-decomposition-which-query-rewrite-actually-moves-recall-1m08)
- SECONDARY (blog). An extra rewrite LLM call adds about 100-500 ms at p50. — [tianpan.co](https://tianpan.co/blog/2026-04-16-rag-query-rewrite-layer)
- SECONDARY. DMQR-RAG reports that diverse multi-query rewriting generally beats single-query rewriting and often beats RAG-Fusion. I did not extract the numbers. — [arXiv 2411.13154](https://pdf.arxiv.org/pdf/2411.13154)
- VERIFIED. Self-Route's failure analysis names multi-step reasoning as the dominant cause of RAG failures on HotpotQA, 2WikiMQA and MuSiQue. That is the case decomposition targets. — [arXiv 2407.16833v2](https://arxiv.org/html/2407.16833v2)

### Inferences
- With a supervised embedder (text-embedding-3-*) plus BM25, the evidence does not support HyDE as a default. The large published gain is over an unsupervised baseline.
- For a personal vault, the bigger win is probably structured query understanding: resolving the target document, date or entity and applying it as a metadata filter, with code applying the typed decision. Decomposition should be used only for comparison or multi-hop intents, chosen by a classifier, because of the -5 point risk on simple lookups. This is an inference and should be validated on our golden set.

### Gaps
- I found no rigorous 2025-26 study of step-back prompting for retrieval recall with modern embedders, and no controlled latency table for any of these methods on OpenAI models.

---

## 5. Chunking: fixed vs semantic vs structure-aware vs parent-child vs late chunking

### Takeaway
The differences are real but modest: up to about 9 recall points in Chroma's study, about 0.60-0.65 answer accuracy across strategies in NVIDIA's. Page-level chunking was the most consistent default in NVIDIA's 2025 benchmark (0.648 average accuracy). For FinanceBench specifically, 1,024-token chunks scored highest (0.579). Structure- and element-aware chunking helps financial reports, and late chunking gives about +1.5-1.9 nDCG@10 points for free with long-context embedders. Overlap adds little.

### Cited Findings
- VERIFIED. NVIDIA (2025), answer accuracy (RAGAS judge):
  - Page-level: average 0.648, SD 0.107, the best average and lowest variance.
  - Token-based averages: 0.603-0.645.
  - FinanceBench: 1,024 tokens 0.579, page-level 0.566, 2,048 tokens 0.506. Section-level was slightly above page-level; no value given.
  - Earnings: 512 tokens 0.681.
  - KG-RAG: page-level 0.520, 128 tokens 0.421.
  - Setup: 15% overlap, top-k 10, nv-embedqa-1b-v2 embeddings, nv-rerankqa-1b-v2 reranker, Nemotron-70B generator.
  - Recommendation: page-level by default. 512-1,024 tokens or section-level can win for financial documents and analytical queries. 128 and 2,048 underperform.
  
  — [NVIDIA blog](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/)
- VERIFIED. Chroma (text-embedding-3-large, 5 corpora including Finance and PubMed, n=5 retrieved chunks), recall:
  - Recursive 800 tokens / 400 overlap (the OpenAI Assistants default): 85.4, lowest efficiency.
  - Recursive 200 tokens / 0 overlap: 88.1 recall, IoU 6.9.
  - Recursive 400 / 0: 89.5.
  - ClusterSemantic 400: 91.3.
  - LLM chunker (GPT-4o): 91.9.
  - Kamradt semantic (default): 83.6, below average.
  - Removing overlap improves efficiency (IoU).
  
  — [Chroma, Evaluating Chunking](https://www.trychroma.com/research/evaluating-chunking)
- VERIFIED. Late chunking (arXiv 2409.04701), BEIR nDCG@10 averaged over SciFact, NFCorpus, FiQA and TREC-COVID with jina-v2-small, jina-v3 and nomic:
  - fixed 256 tokens: 52.2 to 54.0
  - sentence-based: 52.4 to 54.3
  - semantic: 52.4 to 53.8 (about +1.5-1.9 points)
  - Overlap showed no clear advantage.
  - The comparison with LLM contextual enrichment rests on a single toy example (cosine 0.8516 late vs 0.8590 contextual).
  
  — [arXiv 2409.04701](https://arxiv.org/html/2409.04701)
- SECONDARY. Element-based chunking of financial reports (Unstructured, arXiv 2402.05131) starts a new chunk at each title or table element, so tables stay intact. It reports up to 84.4% retrieval accuracy on FinanceBench (page/paragraph-level) and beat token chunking. I did not confirm the number in the paper. — [arXiv 2402.05131](https://arxiv.org/abs/2402.05131v3); [themoonlight review](https://www.themoonlight.io/review/financial-report-chunking-for-effective-retrieval-augmented-generation)
- SECONDARY. A 2026 reproducibility and taxonomy study finds the best chunking strategy is task-dependent. — [arXiv 2602.16974](https://arxiv.org/html/2602.16974v1)

### Inferences
- Late chunking requires a long-context open embedding model run locally (jina, nomic). It is not available through OpenAI embeddings, so it does not fit our stack without a new local model.
- One design fits all three results (NVIDIA page/section consistency, the FinanceBench preference for about 1k tokens, Chroma's efficiency at about 200-400 tokens): retrieve on small structure-aware chunks of about 200-400 tokens, then expand to the parent section or page for the generator (small-to-big). This is an inference; I found no single benchmark of parent-child retrieval in this search.

### Gaps
- I found no quantitative benchmark isolating parent-child (small-to-big) retrieval.
- NVIDIA's per-configuration numbers are mostly in charts, not text.

---

## 6. Long context vs RAG; costs with caching; "lost in the middle" status

### Takeaway
When the relevant documents fit, strong models answer better with full context than with top-k RAG. Self-Route measured 49.70 vs 37.33 (Gemini-1.5-Pro) and 48.67 vs 32.60 (GPT-4o). On FinanceBench, long-context GPT-4-Turbo got 79% correct vs 19% for a shared vector store. Both Anthropic (under 200k tokens) and Databricks (consistent accuracy above 64k only for top models) support whole-document prompting for small corpora. Reliability still degrades with length: Chroma's Context Rot study of 18 models found focused prompts of about 300 tokens beat full prompts of about 113k tokens for every model. A routed hybrid is best supported: whole documents when the scope is one to a few documents under about 100k tokens, RAG otherwise. Self-Route reached about LC quality at 39-65% lower cost.

### Cited Findings
- VERIFIED. Self-Route (Google DeepMind, EMNLP 2024): 300-word chunks, top-5, Contriever retriever, LongBench + ∞Bench.

  | Model | Long context | RAG | Self-Route | Self-Route tokens vs LC |
  |---|---|---|---|---|
  | Gemini-1.5-Pro | 49.70 | 37.33 | 46.41 | 38.39% |
  | GPT-4o | 48.67 | 32.60 | 48.89 | 61.40% |

  - Cost reduction: 65% (Gemini) and 39% (GPT-4o).
  - RAG and LC give identical predictions on about 63% of queries.
  - Raising RAG top-k from 5 to 50 lifts the RAG score from 37.92 to 44.06, vs an LC ceiling of 45.53 in that ablation.
  - The paper has internal inconsistencies between text and tables (noted in the source).
  
  — [arXiv 2407.16833v2](https://arxiv.org/html/2407.16833v2)
- SECONDARY. LaRA (ICML 2025): 2,326 cases over novels, academic papers and financial statements; 11 LLMs. No silver bullet: the better choice depends on model size, long-context capability, context length and task type. Long context does better on reasoning and comparison tasks; RAG does better on some tasks, such as hallucination detection. Summaries report LC +2.4% at 32k and RAG +3.68% at 128k, with stronger models (GPT-4o) favoring LC. I could not confirm these exact figures in the paper. — [arXiv 2502.09977](https://arxiv.org/abs/2502.09977v2); [PMLR](https://proceedings.mlr.press/v267/li25dv.html)
- SECONDARY. Databricks (arXiv 2411.03538, Nov 2024): 20 LLMs, 2k-128k (and up to 2M) tokens, on DocsQA, FinanceBench and NQ. Most models improve and then degrade as context grows. Only a handful of the latest models stay consistent above 64k. Llama-3.1-405B degrades after 32k and GPT-4-0125 after 64k. o1 models led. Gemini 1.5 was lower in accuracy but flat up to 2M tokens. — [arXiv 2411.03538](https://arxiv.org/html/2411.03538v1); [Databricks blog](https://www.databricks.com/blog/long-context-rag-capabilities-openai-o1-and-google-gemini)
- SECONDARY. FinanceBench (arXiv 2311.11944): GPT-4-Turbo with a retrieval system answered incorrectly or refused 81% of questions. The secondary breakdown for the shared vector store is 19% correct, 13% incorrect and 68% failed. Long-context GPT-4-Turbo got 79% correct and 17% incorrect; Claude-2 long-context got 76% correct. — [arXiv 2311.11944](https://arxiv.org/abs/2311.11944v1); [benchmarklist](https://benchmarklist.com/benchmarks/financebench/)
- VERIFIED. Chroma, Context Rot (July 2025), 18 models including GPT-4.1, Claude 4, Gemini 2.5 and Qwen3:
  - Performance grows "increasingly unreliable as input length grows", even on trivial tasks.
  - One distractor hurts, and four hurt more.
  - Low question-to-needle similarity degrades faster.
  - Needle position had no notable effect in their NIAH runs.
  - LongMemEval: focused prompts (about 300 tokens) scored significantly higher than full prompts (about 113k) for all models.
  
  — [Chroma, Context Rot](https://www.trychroma.com/research/context-rot)
- VERIFIED. Anthropic: under 200k tokens, consider skipping RAG. Caching gives more than 2x latency and up to 90% cost reduction. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- SECONDARY, third-party mirror citing OpenAI docs, page dated Oct 2026, UNVERIFIED against OpenAI:

  | Model | Input $/M | Cached input $/M | Output $/M |
  |---|---|---|---|
  | GPT-6 Luna | 0.10 | 0.01 | 0.50 |
  | GPT-6.1 Terra | 1.00 | 0.05 | 5.00 |
  | GPT-6.1 Sol | 2.00 | 0.10 | 10.00 |
  | GPT-6 Astra | 10.00 | 1.00 | 50.00 |

  Cache writes cost 1.25x input (30-minute retention) or 2.5x (12-hour). A long-context price band also exists. — [morphllm OpenAI pricing](https://www.morphllm.com/openai-api-pricing)
- SECONDARY. Older OpenAI caching: automatic for prompts of 1,024 tokens or more, cached input billed at 10% of the input price, no write premium. Sources conflict on whether newer models add a write premium. — [DEV Community](https://dev.to/rikuq/openai-prompt-caching-explained-automatic-free-to-enable-90-off-cached-input-tokens-7bn); [CometAPI](https://www.cometapi.com/cached-input-pricing-of-gpt-5-6-and-gemini-3-6-falsh-what-it-costs/)

### Inferences
- For documents under about 50-100k tokens, full context is generally better when the question is scoped to one or a few known documents and the model is strong. That is the regime of Self-Route (+12-16 points for LC), FinanceBench (79% vs 19%) and Databricks (top models flat to 64k+). Context Rot warns that a focused context still wins when it captures the answer. So the evidence favors a routed design over all-or-nothing: whole documents for document-scoped questions, retrieval for vault-wide questions.
- Cost at the unverified prices: one 100k-token document on Terra costs about $0.10 per question uncached, about $0.125 for the first question including the cache write, and about $0.005 per follow-up while cached. On Luna it costs about $0.01 uncached. This is my arithmetic.
- Citation handles still work with whole-document context if the document is passed as handle-tagged sentences. That keeps the application-managed citation rule intact, but it inflates token count.

### Gaps
- I found no 2025-26 study of the newest OpenAI models (GPT-5.x/6.x) on long-context vs RAG for contracts or 10-Ks.
- LaRA's exact numbers are unconfirmed.
- Whether OpenAI's current caching charges a write premium is unconfirmed from a primary source.

---

## 7. Embedding model choice and pgvector index tradeoffs

### Takeaway
On MTEB average, text-embedding-3-small scores 62.3 and -large 64.6. On MIRACL (multilingual retrieval) they score 44.0 vs 54.9, and ada-002 scores 61.0 / 31.4. Matryoshka truncation is strong: -large at 256 dimensions reportedly beats ada-002 at 1,536 on MTEB. For a vault of thousands of documents (well under 1M chunks), halfvec halves storage at a small recall cost. HNSW build time and memory are the main 1M-scale costs, and no clean independent halfvec benchmark was found.

### Cited Findings
- SECONDARY (OpenAI's own page returned 403). MTEB average: ada-002 61.0, 3-small 62.3, 3-large 64.6. MIRACL: 31.4, 44.0, 54.9. -large at 256 dimensions beats ada-002 at 1,536 dimensions on MTEB. — [Pinecone](https://www.pinecone.io/learn/openai-embeddings-v3/); [Vercel model page](https://vercel.com/ai-gateway/models/text-embedding-3-large)
- SECONDARY. halfvec (pgvector 0.7+) is 16-bit, indexable up to 4,000 dimensions, and roughly halves index and storage size. AWS measured a 50% storage reduction and warns of possible accuracy, speed and resource impacts. — [AWS Aurora blog](https://aws.amazon.com/de/blogs/database/load-vector-embeddings-up-to-67x-faster-with-pgvector-and-amazon-aurora/)
- SECONDARY (practitioner rules of thumb, not measured). The halfvec recall drop is "usually <1%". — [BigData Boutique, pgvector in production](https://bigdataboutique.com/blog/pgvector-in-production)
- SECONDARY. At 1M vectors (50 dimensions, float32), HNSW took 4,065 s to build vs 128 s for IVFFlat. At recall 0.998, HNSW needed about 729 MB vs 257 MB for IVFFlat. Parallel HNSW builds in pgvector 0.7 are up to 30x faster than in 0.5.1. — [Tembo](https://temboio.substack.com/p/vector-indexes-in-postgres-using); [AWS](https://aws.amazon.com/de/blogs/database/load-vector-embeddings-up-to-67x-faster-with-pgvector-and-amazon-aurora/)
- SECONDARY. In a vendor test, halfvec HNSW at 1M showed a 2.05 GB index and 122 ms p50 at 95% recall@10. — [braindetox.kr](https://braindetox.kr/en/posts/pg_vectorsearch_prism_index_vs_pgvector_2026.html)

### Inferences
- Thousands of PDFs is probably about 100k-1M chunks. Per-owner partitions are much smaller, so exact search or HNSW over halfvec should give near-exact recall. Embedding-model quality (the -small to -large MTEB gap is 2.3 points; MIRACL's is 10.9 for any non-English content) matters more than index tuning.
- Re-embedding with -large at 1,024 or 1,536 dimensions on halfvec is a cheap experiment. It should go through the paired test decision.

### Gaps
- I found no MTEB v2 retrieval-only scores for OpenAI vs current open models (e.g., Qwen3-Embedding, jina-v3) from a primary source in this search.
- I found no independent halfvec-vs-vector recall benchmark at 1M.

---

## 8. Table-heavy documents (10-K/10-Q)

### Takeaway
Keep tables intact as their own chunks, splitting at structural element boundaries. Serialize them in a structured text form: Markdown wins most general benchmarks, and JSON won in one small financial study. Full-document context also helps a lot on FinanceBench (79% vs 19%). For numeric 10-K questions, the controlled evidence favors structure-aware chunking with about 512-1,024-token or section-sized chunks.

### Cited Findings
- SECONDARY. Element-type chunking of financial reports (new chunk at each title or table) beat token chunking on FinanceBench retrieval. The reported figure is up to 84.4% (unconfirmed). — [arXiv 2402.05131](https://arxiv.org/abs/2402.05131v3)
- VERIFIED. NVIDIA: on FinanceBench, 1,024-token chunks scored 0.579 vs page-level 0.566 vs 2,048 tokens 0.506. On Earnings, 512 tokens scored 0.681. — [NVIDIA](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/)
- SECONDARY. TQA-Bench: Markdown was the best serialization for most models, compared with CSV, JSON and HTML. HTML is token-heavy. — [arXiv 2411.19504](https://arxiv.org/pdf/2411.19504)
- SECONDARY. A financial-document study with 75 QA pairs found JSON better than dataframe-style tables for GPT-4 and GPT-4o. — [arXiv 2506.05182](https://arxiv.org/pdf/2506.05182)
- SECONDARY, VENDOR. Egnyte chose Markdown: HTML is faithful but token-costly, and flat formats lose hierarchy. — [Egnyte](https://www.egnyte.com/blog/post/beyond-plain-text-egnytes-journey-to-structured-data-extraction-in-rag-systems)
- SECONDARY. FinanceBench long-context GPT-4-Turbo got 79% correct vs 19% for a shared vector store. — [benchmarklist](https://benchmarklist.com/benchmarks/financebench/)

### Inferences
- For our citation model, the stored `original_text` must remain the glyph-mapped stream. Any Markdown or JSON table serialization should be an index-side or prompt-side view, not cited text. Headers repeated into each table chunk (a contextual-BM25-style prefix) are a natural fit.
- Many FinanceBench failures are retrieval failures: picking the wrong filing or period. Metadata filters on company, fiscal period and document type are likely high-leverage. This is an inference; I found no direct number.

### Gaps
- I found no controlled, finance-specific benchmark comparing Markdown, HTML and JSON table serializations for retrieval recall, as opposed to answer accuracy.
- The FinanceBench 13% incorrect split is unconfirmed from the primary table.
