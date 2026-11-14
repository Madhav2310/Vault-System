# Architecture choices of leading open-source document RAG / document-chat systems (2025-2026)

Research date: 2026-11-13. Repo metadata (stars, license SPDX, last push) was pulled live from the GitHub REST API (`https://api.github.com/repos/<owner>/<repo>`) on that date. Source-code claims were verified by reading raw files on each repo's default branch that day. Paths are given so they can be re-checked; default branches move, so line numbers will drift.

## Q1. Per-project architecture: parser, chunking, vector store, keyword search, reranker, citations, streaming, eval, background jobs, code organization

### Takeaway
The mature projects agree on hybrid retrieval, a reranker and async ingestion workers. They differ most in citations. Onyx, LlamaIndex and Haystack use numbered chunk markers `[n]` that code maps back to documents. PaperQA2 uses per-document citation keys over LLM-written evidence summaries. RAGFlow's basic ("naive") pipeline adds markers after generation by embedding similarity. Kotaemon has the model quote start and end phrases, then fuzzy-matches them. None of them, as far as the code read shows, ties citations to sentences with deterministic handles. Vault's E3.1 scheme is finer-grained than anything surveyed.

### Cited Findings

**Repo vitals (GitHub API, 2026-11-13)**
- RAGFlow (infiniflow/ragflow): 94,118 stars, Apache-2.0, pushed 2026-11-13. v1.0.0 listed 2026-11-06 — [GitHub API](https://api.github.com/repos/infiniflow/ragflow); [README](https://github.com/infiniflow/ragflow)
- Onyx (onyx-dot-app/onyx): 33,373 stars, SPDX "NOASSERTION" (MIT Community Edition plus a separately licensed Enterprise Edition), pushed 2026-11-13 — [GitHub API](https://api.github.com/repos/onyx-dot-app/onyx); [README](https://github.com/onyx-dot-app/onyx)
- Kotaemon (Cinnamon/kotaemon): 25,910 stars, Apache-2.0, last push 2026-07-14, so activity is slowing — [GitHub API](https://api.github.com/repos/Cinnamon/kotaemon)
- PaperQA (Future-House/paper-qa): 9,595 stars, Apache-2.0, pushed 2026-11-09 — [GitHub API](https://api.github.com/repos/Future-House/paper-qa)
- Khoj (khoj-ai/khoj): 38,120 stars, AGPL-3.0, last push 2026-08-02 — [GitHub API](https://api.github.com/repos/khoj-ai/khoj)
- R2R (SciPhi-AI/R2R): 8,093 stars, MIT, last push 2025-11-07. That is about a year with no pushes, so treat it as effectively dormant — [GitHub API](https://api.github.com/repos/SciPhi-AI/R2R)
- open-notebook (lfnovo/open-notebook): 41,924 stars, MIT, pushed 2026-11-12 — [GitHub API](https://api.github.com/repos/lfnovo/open-notebook)
- Morphik (morphik-org/morphik-core): 3,720 stars, Business Source License 1.1 ("source-available"), pushed 2026-11-04 — [README](https://github.com/morphik-org/morphik-core)
- Verba (weaviate/Verba): 7,693 stars, BSD-3-Clause, archived and read-only since 2026-06-08 ("Verba is no longer in active development") — [README](https://github.com/weaviate/Verba)
- AnythingLLM (Mintplex-Labs/anything-llm): 68,243 stars, MIT, pushed 2026-11-13 — [GitHub API](https://api.github.com/repos/Mintplex-Labs/anything-llm)
- LlamaIndex (run-llama/llama_index): 52,888 stars, MIT. Haystack (deepset-ai/haystack): 27,002 stars, Apache-2.0. Both active as of 2026-11 — [GitHub API](https://api.github.com/repos/run-llama/llama_index); [GitHub API](https://api.github.com/repos/deepset-ai/haystack)

**RAGFlow**
- The 1.0 line is mostly a Go rewrite. The default-branch tree has about 3,210 `.go` files against 167 `.py` files, and the old Python paths such as `rag/nlp/search.py` now return 404. Core code lives under `internal/` (`internal/deepdoc/parser/pdf`, `internal/ingestion/component/chunker`, `internal/agentic_rag`, `internal/service`) — [repo tree](https://github.com/infiniflow/ragflow/tree/main/internal)
- Parsing: DeepDoc does layout analysis, OCR and table-structure recognition, and "uses CPU inference" in 1.0. Model files live in `internal/rag/res/deepdoc` — [README](https://github.com/infiniflow/ragflow)
- Chunking is template-based ("Plenty of template options"). The Go chunker package has per-template modules, including `general`, `manual`, `one`, `page`, `qa`, `table`, `hierarchy`, `group` and `sentence_boundary`, plus `general_pdf_source_positions.go` and `positions_slice.go`, which carry page and position data with each chunk — [chunker dir](https://github.com/infiniflow/ragflow/tree/main/internal/ingestion/component/chunker)
- Document engines: Elasticsearch (default `es01` in compose) and Infinity. The tree also has `internal/engine/oceanbase`, `internal/engine/serenedb` and `internal/engine/vastbase` search adapters. Retrieval is "Multiple recall paired with fused re-ranking", and an Agentic Retrieval mode runs at four thinking levels — [README](https://github.com/infiniflow/ragflow); [engine dir](https://github.com/infiniflow/ragflow/tree/main/internal/engine)
- Citations, basic pipeline: `InsertCitations` in `internal/service/citation.go` splits the answer into sentences and embeds them. It builds a cosine-similarity matrix against the chunk vectors, then runs a threshold descent from 0.63 down to 0.3, multiplying by 0.8 each round. A chunk is cited when its similarity exceeds 0.99 times the sentence's best match, up to 4 chunks per sentence. Finally it appends ` [ID:n]` markers — [citation.go](https://raw.githubusercontent.com/infiniflow/ragflow/main/internal/service/citation.go)
- Citations, agentic pipeline: tool output prints `chunk_id: <id>`, and the model must repeat that id on every factual line. Code then extracts the ids deterministically and appends `[ID:N]` markers, where N indexes `reference.chunks`. The file's own comment says the basic pipeline "earns its [ID:N] markers two probabilistic ways: a prompt asking the model to emit them, and an embedding-similarity pass (InsertCitations) guessing which sentence came from which chunk". It calls the agentic version "deterministic" and says ids it cannot resolve "stay unmarked rather than pointing at a wrong index" — [citations.go](https://raw.githubusercontent.com/infiniflow/ragflow/main/internal/agentic_rag/citations.go)
- Background jobs: an "Ingestor" service handles parsing and ingestion tasks, and a "Syncer" handles external data-source sync — [README](https://github.com/infiniflow/ragflow)

**Onyx (formerly Danswer)**
- Document index: the main code is now OpenSearch (`backend/onyx/document_index/opensearch/{search.py,schema.py,opensearch_document_index.py,...}`). Vespa survives only in old Alembic migrations and `backend/ee/onyx/background/celery/tasks/vespa`, which points to a Vespa-to-OpenSearch migration — [repo tree](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/document_index). Earlier history: Danswer had moved to Vespa "for better normalization across vector and keyword scores" and for "multi-pass indexing" — [ZenML case study](https://www.zenml.io/llmops-database/scaling-enterprise-rag-with-advanced-vector-search-migration)
- Hybrid search does not use RRF. It uses an OpenSearch normalization-processor search pipeline with `min_max` normalization (a `zscore` variant is also defined) and weights of 0.5 vector and 0.5 keyword. Title and content share one keyword weight, and an assert checks that the weights sum to 1.0 — [opensearch/search.py](https://raw.githubusercontent.com/onyx-dot-app/onyx/main/backend/onyx/document_index/opensearch/search.py)
- Chunking (`backend/onyx/indexing/chunker.py`):
  - Overlap is deliberately zero: `CHUNK_OVERLAP = 0`, with the comment "Not supporting overlaps, we need a clean combination of chunks".
  - Multipass indexing adds `MINI_CHUNK_SIZE` and `LARGE_CHUNK_RATIO`. `generate_large_chunks` combines N adjacent chunks and records `large_chunk_reference_ids`.
  - Each chunk carries a `title_prefix` and separate `metadata_suffix_semantic` / `metadata_suffix_keyword` fields.
  - Contextual RAG is supported: a `contextual_rag_reserved_tokens` budget is set aside, controlled by `USE_CHUNK_SUMMARY` / `USE_DOCUMENT_SUMMARY`, with the prompt in `backend/onyx/prompts/contextual_retrieval.py`.
  - Sub-chunkers exist per section type: text, image and tabular.
  
  Sources: [chunker.py](https://raw.githubusercontent.com/onyx-dot-app/onyx/main/backend/onyx/indexing/chunker.py); [chunking dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/indexing/chunking)
- Citations and streaming: `backend/onyx/chat/citation_processor.py` (777 lines) is a "Dynamic Citation Processor" that can "Process token streams from LLMs to extract citations". It works in three modes: "REMOVE, KEEP_MARKERS, or HYPERLINK". It emits `CitationInfo` objects and keeps a `CitationMapping: dict[int, SearchDoc]`, so citations are document or chunk level, numbered `[n]`, and resolved while the answer streams. It handles markdown code fences and list markers so markers inside code are not rewritten — [citation_processor.py](https://raw.githubusercontent.com/onyx-dot-app/onyx/main/backend/onyx/chat/citation_processor.py)
- Background jobs are split into separate Celery apps in `backend/onyx/background/celery/apps/`: `primary`, `beat`, `light`, `heavy`, `docfetching`, `docprocessing`, `monitoring`, `user_file_processing` and `scheduled_tasks`. Fetching and processing run on separate worker pools — [apps dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/background/celery/apps)
- Product framing: "hybrid index + a custom agent harness tuned for information retrieval", 50+ connectors, Deep Research — [README](https://github.com/onyx-dot-app/onyx)

**Kotaemon**
- Parsers: Azure Document Intelligence, Adobe PDF Extract, Docling, PaddleOCR, and Unstructured for other file types. The retriever is "hybrid (full-text & vector) retriever and re-ranking". GraphRAG options are nano-GraphRAG, LightRAG and Microsoft GraphRAG. Reasoning pipelines are `FullQAPipeline`, `FullDecomposeQAPipeline`, `ReactAgentPipeline` and `RewooAgentPipeline` — [README](https://github.com/Cinnamon/kotaemon)
- Code layout: `libs/kotaemon` is a pip-installable framework of components and pipelines. `libs/ktem` is the Gradio app, with reasoning pipelines in `libs/ktem/ktem/reasoning/` and indices in `libs/ktem/ktem/index/file/`. `flowsettings.py` wires pipelines into the UI — [README](https://github.com/Cinnamon/kotaemon)
- Citation mode 1: `CitationPipeline` in `libs/kotaemon/kotaemon/indices/qa/citation.py` forces a tool call (`tool_choice: "required"`) to a `CiteEvidence` schema with `evidences: List[str]`, described as "a direct quote from the context, as a substring of the original content (max 15 words)". It asks for "maximum 5" evidences, but neither limit is enforced, and the file does not verify the quotes — [citation.py](https://raw.githubusercontent.com/Cinnamon/kotaemon/main/libs/kotaemon/kotaemon/indices/qa/citation.py)
- Citation mode 2: the inline mode in `citation_qa_inline.py` asks the model to write `FINAL ANSWER` with `【n】` markers, followed by a `CITATION LIST` of `CITATION【n】 / START_PHRASE: ... / END_PHRASE: ...`. Code parses this with `CITATION_PATTERN = r"citation【(\d+)】"` and calls `find_start_end_phrase`, which in `utils.py` uses `difflib.SequenceMatcher` fuzzy matching to find the span in the source chunk — [citation_qa_inline.py](https://raw.githubusercontent.com/Cinnamon/kotaemon/main/libs/kotaemon/kotaemon/indices/qa/citation_qa_inline.py); [utils.py](https://raw.githubusercontent.com/Cinnamon/kotaemon/main/libs/kotaemon/kotaemon/indices/qa/utils.py)

**PaperQA2**: see Q3.
- Code layout: a flat `src/paperqa/` package with `docs.py` (the Docs corpus object), `core.py`, `readers.py`, `prompts.py`, `settings.py` (Pydantic settings) and `agents/tools.py` — [repo tree](https://github.com/Future-House/paper-qa/tree/main/src/paperqa)

**Khoj**
- Django models with `pgvector.django.VectorField`, so the store is Postgres + pgvector. The default bi-encoder is `thenlper/gte-small` and the default cross-encoder is `mixedbread-ai/mxbai-rerank-xsmall-v1`, both configurable per `SearchModelConfig` — [models/__init__.py](https://raw.githubusercontent.com/khoj-ai/khoj/master/src/khoj/database/models/__init__.py)
- Chunking: `split_entries_by_max_tokens(max_tokens=256)` using LangChain's `RecursiveCharacterTextSplitter` — [text_to_entries.py](https://raw.githubusercontent.com/khoj-ai/khoj/master/src/khoj/processor/content/text_to_entries.py)
- Search filters on a `bi_encoder_confidence_threshold` max distance, then `rerank_and_sort_results` with the cross-encoder — [text_search.py](https://raw.githubusercontent.com/khoj-ai/khoj/master/src/khoj/search_type/text_search.py)

**R2R (SciPhi)**
- Features: "Semantic + keyword search with reciprocal rank fusion", "RAG with citations", automatic entity and relationship extraction, Deep Research. Multimodal ingestion covers .txt, .pdf, .json, .png, .mp3 and more — [README](https://github.com/SciPhi-AI/R2R)
- Provider-interface architecture: `py/core/base/providers/{auth,crypto,database,email,embedding,file,ingestion,llm,ocr,orchestration,scheduler}.py`. Orchestration has two backends, `py/core/main/orchestration/hatchet/{ingestion_workflow,graph_workflow}.py` (Hatchet durable workflows with `@orchestration_provider.step(retries=0, timeout="60m")` on `parse`) and `.../orchestration/simple` for in-process runs — [repo tree](https://github.com/SciPhi-AI/R2R/tree/main/py/core); [ingestion_workflow.py](https://raw.githubusercontent.com/SciPhi-AI/R2R/main/py/core/main/orchestration/hatchet/ingestion_workflow.py)

**open-notebook**
- Stack: Python, FastAPI, Next.js and SurrealDB, with a background worker beside the API. It offers "Full-text and vector search across all your content" and "answers with proper source citations" — [README](https://github.com/lfnovo/open-notebook)
- Jobs use the `surreal_commands` library (`from surreal_commands import CommandInput, CommandOutput, command`) in `commands/{source,embedding,podcast}_commands.py`. The job queue lives in the database, not in Celery or Redis — [source_commands.py](https://raw.githubusercontent.com/lfnovo/open-notebook/main/commands/source_commands.py)
- Chunking uses LangChain `HTMLHeaderTextSplitter`, `MarkdownHeaderTextSplitter` and `RecursiveCharacterTextSplitter` (`open_notebook/utils/chunking.py`). Flows live in `open_notebook/graphs/{ask,chat,source,source_chat,transformation,tools}.py` — [chunking.py](https://raw.githubusercontent.com/lfnovo/open-notebook/main/open_notebook/utils/chunking.py); [repo tree](https://github.com/lfnovo/open-notebook/tree/main/open_notebook/graphs)

**Morphik**
- Multimodal retrieval through ColPali: `core/embedding/colpali_embedding_model.py` and `colpali_api_embedding_model.py`, plus `litellm_embedding.py`. It extracts "bounding boxes, labeling, classification" metadata and does rules-based ingestion — [README](https://github.com/morphik-org/morphik-core); [repo tree](https://github.com/morphik-org/morphik-core/tree/main/core)
- The default reranker is `FlagReranker` with `BAAI/bge-reranker-v2-gemma` — [flag_reranker.py](https://raw.githubusercontent.com/morphik-org/morphik-core/main/core/reranker/flag_reranker.py)
- The `core/parser/` package has `base_parser.py`, `morphik_parser.py` and `xml_chunker.py`. Services are under `core/services/` (`ingestion_service.py`, `document_service.py`), and there is an `evaluations/` top-level directory — [repo tree](https://github.com/morphik-org/morphik-core/tree/main/core)

**Verba (archived)**
- Plugin-style components:
  - Readers: Unstructured, Firecrawl, Upstage, native PDF/CSV/DOCX, GitHub/GitLab, AssemblyAI.
  - Chunkers: Token and Sentence (spaCy), Semantic ("group by semantic sentence similarity"), Recursive, HTML, Markdown, Code, JSON.
  - Embedders: seven providers.
  - Retrieval: Weaviate hybrid search ("Semantic Search combined with Keyword Search").
  - Generators: seven providers.
  
  Source: [README](https://github.com/weaviate/Verba)

**AnythingLLM**
- Three Node.js services: `server/` (Express: chat, vector DB, LLM), `collector/` (document parsing) and `frontend/` (Vite/React). LanceDB is the default vector store, with PGVector, Pinecone, Chroma, Weaviate, Qdrant, Milvus and others supported — [README](https://github.com/Mintplex-Labs/anything-llm)
- The text splitter defaults to `chunkSize = 1000` and `chunkOverlap = 20`, capped at the embedder's limit (`determineMaxChunkSize`) — [TextSplitter/index.js](https://raw.githubusercontent.com/Mintplex-Labs/anything-llm/master/server/utils/TextSplitter/index.js)

**LlamaIndex: canonical CitationQueryEngine**
- Retrieved nodes (default 1024 chunk size) are re-split into `citation_chunk_size` (default 512) pieces labeled `Source 1:`, `Source 2:` and so on. The model cites `[N]`, and N maps to `response.source_nodes[N-1]`. Re-splitting larger than the node size "will have no effect" — [docs](https://developers.llamaindex.ai/python/examples/query_engine/citation_query_engine/)
- Prompt text: "Please provide an answer based solely on the provided sources. When referencing information from a source, cite the appropriate source(s) using their corresponding numbers. Every answer should include at least one source citation. Only cite a source when you are explicitly referencing it. If none of the sources are helpful, you should indicate that." — [citation_query_engine.py](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/query_engine/citation_query_engine.py)

**Haystack: canonical AnswerBuilder**
- `AnswerBuilder` takes a `reference_pattern`. The default is `r"\[(\d+)\]"`, with an expanded form `\[(\d+(?:[,-]\d+)*)\]` for ranges such as [1-3] and lists such as [1,2]. Code uses it to attach only the referenced documents to the `GeneratedAnswer`, via `return_only_referenced_documents` — [answer_builder.py](https://raw.githubusercontent.com/deepset-ai/haystack/main/haystack/components/builders/answer_builder.py)

### Inferences
- Citation designs fall on a spectrum from probabilistic to deterministic:
  - RAGFlow's basic pipeline: post-hoc embedding similarity. Its own maintainers call it "guessing".
  - Kotaemon: model-written quotes plus fuzzy matching. Vault's rule "never trust model-written quotes" deliberately rejects this.
  - LlamaIndex, Haystack and Onyx: numbered chunk markers.
  - RAGFlow's agentic pipeline: echoed chunk ids, extracted by code, with unresolved ids left unmarked. This is the closest analog to Vault's handles, but at chunk granularity.
  
  Vault's sentence handles plus code-copied `cited_text` is the deterministic end of the spectrum, at finer granularity than any project surveyed. That is evidence the approach is uncommon rather than reinvented. RAGFlow 1.0 independently moving toward deterministic id extraction supports the design choice.
- Onyx's streaming citation processor shows what streaming plus citations needs: an incremental marker parser that is aware of code fences, with a number-to-document map. If Vault streams answers containing handles, it needs the same kind of incremental parser (Onyx is MIT, so the idea is borrowable). Onyx's design is the best reference here.
- Onyx uses min-max normalized weighted fusion (0.5/0.5), not RRF; R2R uses RRF. Both are defensible. RRF is easier to justify without score calibration, while weighted normalization exposes a tunable knob that suits Vault's convergence engine.
- Onyx's zero-overlap chunks, mini/large multipass chunks, separate semantic and keyword metadata suffixes, and contextual-RAG token reserve are concrete, battle-tested chunking patterns, more directly reusable than RAGFlow's DeepDoc templates.
- Onyx's Celery split (fetching, processing, light, heavy, beat, monitoring) is the closest production analog to Vault's Celery/Redis stack. Its separation of heavy from light queues is worth imitating.
- RAGFlow's Python-to-Go rewrite makes its older Python code (widely copied in 2024-2025 tutorials) a stale reference. Cite the Go `internal/` paths instead.

### Gaps
- RAGFlow's 1.0 hybrid fusion weights could not be found by file name in the Go tree. The old Python version reportedly used weighted-sum fusion, but this was not verified for 1.0.
- RAGFlow's full template list (naive/book/paper/laws/presentation/resume/picture, etc.) was not enumerated from 1.0 docs. Only the Go chunker module names were seen.
- Onyx's reranker setup (which cross-encoder, whether on by default) and its exact `MINI_CHUNK_SIZE` / `LARGE_CHUNK_RATIO` values were not read.
- Streaming transport (SSE vs WebSocket) was confirmed only for RAGFlow, whose code comments mention "SSE payload", and Onyx, which processes token streams. It was not checked for the others.
- Evaluation tooling: only PaperQA2 (LitQA2, see Q3) and Morphik (an `evaluations/` dir) showed evaluation assets. No eval harness was found or read for RAGFlow, Onyx, Kotaemon, Khoj, open-notebook or AnythingLLM. That is not proof none exists.
- R2R's reranker and chunker defaults and Khoj's citation format were not read.

## Q2. Which projects implement exact-passage highlighting in PDFs, and how?

### Takeaway
Two approaches appear. RAGFlow stores layout positions (page plus box coordinates) on each chunk and draws chunk-level highlight boxes from them. Kotaemon finds the text in the PDF.js text layer with a fuzzy span-matching ratio. Neither highlights at sentence level by design, and RAGFlow has open bugs where positions get lost at chunk overlaps.

### Cited Findings
- RAGFlow chunks carry source positions from the PDF parser: `internal/ingestion/component/chunker/general_pdf_source_positions.go`, `positions_slice.go` and `position_merge_test.go` — [chunker dir](https://github.com/infiniflow/ragflow/tree/main/internal/ingestion/component/chunker)
- RAGFlow issue #18148: "Parsing-result chunk highlight missing/inaccurate for PDF (Python chunker drops overlap-head coordinates)". The text that overlaps between chunks loses its coordinates, so the highlight misses that part. Highlighting is PDF-only by design — [Issue #18148](https://github.com/infiniflow/ragflow/issues/18148). This was seen through a search-result summary and not read in full.
- RAGFlow README: "Visualization of text chunking to allow human intervention" and "Quick view of the key references and traceable citations" — [README](https://github.com/infiniflow/ragflow)
- Kotaemon: "View your citations (incl. relevant score) directly in the in-browser PDF viewer with highlights". PDF.js is an optional download (`scripts/download_pdfjs.sh`) — [README](https://github.com/Cinnamon/kotaemon)
- How Kotaemon's highlighting works, in `libs/ktem/ktem/assets/js/pdf_viewer.js`:
  - It embeds `<pdfjs-viewer-element ... phrase="true">`.
  - `compareText(search_phrases, page_label)` walks `div.textLayer > span` on the target page.
  - It wraps any span where `matchRatio(phrase, span.textContent) > 0.5` in `<span class='highlight selected'>`.
  - It retries on a 2000 ms timeout while the page renders.
  - Search phrases come from the highlighted evidence spans in the answer, with `【id】` markers stripped by regex.
  
  This is span-granular fuzzy text-layer matching, not coordinate-based. Source: [pdf_viewer.js](https://raw.githubusercontent.com/Cinnamon/kotaemon/main/libs/ktem/ktem/assets/js/pdf_viewer.js)
- Morphik extracts "bounding boxes" as document metadata. Whether its UI highlights cited passages was not confirmed — [README](https://github.com/morphik-org/morphik-core)
- PaperQA2 cites page ranges in its keys, for example "(Example2012Example pages 3-4)", and does not highlight passages — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)

### Inferences
- If Vault stores per-sentence character offsets mapped to glyph boxes (research 05, invariant I1), it can highlight exactly, deterministically, at sentence level. That is strictly better than both Kotaemon's fuzzy match above a 0.5 ratio and RAGFlow's chunk-level boxes. RAGFlow's overlap bug is a warning: if chunks overlap, positions must be carried per character or per sentence, not per chunk.
- Kotaemon's PDF.js text-layer method is a cheap fallback when no coordinates exist. But PDF.js text-layer spans do not map one-to-one to glyph-stream sentences, so it is approximate.

### Gaps
- Whether Onyx, open-notebook, AnythingLLM or Khoj highlight inside PDFs was not verified. The READMEs mention only "source citations".
- The exact RAGFlow position tuple format in 1.0 (page, x0, x1, top, bottom) was not read from code.

## Q3. PaperQA2: evidence gathering and contextual summarization loop, and citation accuracy numbers

### Takeaway
PaperQA2 runs an agent that loops over its tools: paper search, gather evidence, citation traversal and generate answer. Gather Evidence retrieves the top-k chunks by vector search. An LLM then summarizes each chunk for the question and gives it a 0-10 relevance score (RCS, "reranking and contextual summarization"). Only the top summaries reach the answer prompt, which cites per-document keys with page ranges. The reported numbers:
- LitQA2: 85.2% precision against 73.8% for human experts.
- WikiCrow: 86.1% cited-claim precision against 71.2% for Wikipedia.

Ablations show RCS is the component that matters most.

### Cited Findings
- Agent tools:
  - Paper Search: keyword query; 12 candidate papers by default, then parsed and chunked.
  - Gather Evidence: top-k dense retrieval, then RCS.
  - Citation Traversal: one degree of references and citers, filtered by citation overlap.
  - Generate Answer.
  
  The agent model in the paper is gpt-4-turbo-2024-04-09 — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- RCS: each top-k chunk gets one LLM call returning JSON `{"summary", "relevance_score"}`. Chunks are re-ranked by that score. Summaries run about 200-400 tokens, against a standard chunk of about 2,250 tokens. Paper settings: top-k 30 for LitQA and 25 for WikiCrow, with 15 summaries passed to Generate Answer — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- Current code defaults differ from the paper settings: `evidence_k = 10`, `evidence_relevance_score_cutoff = 1` (evidence scoring 0 is dropped), `evidence_summary_length = "about 100 words"`, `answer_max_sources = 5`, `evidence_skip_summary = False`, and `reader_config = {"chunk_chars": 5000, "overlap": 250}`. The paper reports 750-character overlap, so the defaults have drifted — [settings.py](https://raw.githubusercontent.com/Future-House/paper-qa/main/src/paperqa/settings.py); [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- The summary prompt asks that, if the chunk is irrelevant, the model "leave `summary` empty, and make `relevance_score` be 0". A multimodal variant adds `used_images`. The answer prompt says to cite "via citation keys at the end of sentences ... Only cite from the context above and only use the citation keys from the context ... Do not concatenate citation keys" — [prompts.py](https://raw.githubusercontent.com/Future-House/paper-qa/main/src/paperqa/prompts.py)
- Parsers: PyMuPDF by default for LitQA and Grobid for WikiCrow. Grobid parsing and larger chunks "did not significantly change precision, accuracy, or recall on LitQA2" — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- LitQA2 (248 questions):
  - PaperQA2: precision 85.2% ± 1.1%, accuracy 66.0% ± 1.2%, "insufficient information" 21.9%, about 14.5 papers parsed per question.
  - Human experts (n=9): precision 73.8% ± 9.6%, accuracy 67.7% ± 11.9%. Precision is superhuman (p=0.0036); accuracy is not significantly different (p=0.66).
  - Original PaperQA: accuracy 36.7%, precision 76.5%.
  
  Source: [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- WikiCrow (240 articles), compared with Wikipedia:
  - "Cited and unsupported" statements: 13.5% vs 24.9% (p=0.0075).
  - Uncited statements: 3.5% vs 13.6%.
  - Precision (cited and supported over all cited): 86.1% vs 71.2% (p=0.0013).
  - Reasoning errors: 12 vs 26.
  - Cost: about $4.48 and 491.5 s per article.
  
  Source: [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- Ablations:
  - Removing RCS significantly reduced retrieval accuracy (t=9.29, p<0.001).
  - Smaller RCS models (GPT-3.5-Turbo, Llama3-70B) did worse than no RCS model at all. GPT-4-Turbo was best (p=0.003).
  - Accuracy rose with top-k from 1 to 10 (p=0.014), with diminishing returns from 10 to 30.
  - 15 summaries gave the best precision and 5 the best accuracy.
  - The non-agentic version had lower accuracy (p=0.015).
  
  Source: [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- Contradiction detection (ContraCrow): AUC 0.842. At threshold 8: 73% accuracy, 88% precision, 7% false positives — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)

### Inferences
- RCS amounts to an LLM reranker plus per-question compression. A key caveat for Vault: summaries are LLM-written text, and the answer cites keys over those summaries, not over original sentences. That violates Vault's rule that "only `chunks.original_text` is evidence". One option is to use RCS as a relevance filter and scorer only (keep the 0-10 score, drop the summary from the evidence path), or to have it return sentence handles it found relevant rather than prose. The ablation finding that a weak model doing RCS is worse than none argues for routing it to `balanced`/`deep`, not `fast`, and for measuring it against the cross-encoder in the convergence engine before adopting it.
- PaperQA2's citation precision is measured as cited claims supported, judged by humans. That metric maps directly onto a Vault eval: the fraction of answer sentences whose cited handles entail the claim.

### Gaps
- Exact figures for Perplexity and Elicit on LitQA2 were not captured; only the qualitative statement that PaperQA2 beats them was.
- The direction of the citation-traversal ablation effect on accuracy (p=0.069) is worded ambiguously in the text.
- No independent replication of the LitQA2 numbers was found.

## Q4. Design lessons, post-mortems and known weaknesses

### Takeaway
The clearest lessons come from the projects' own code comments and project status, not from published post-mortems:
- Post-hoc citation attribution is unreliable (RAGFlow's own comment).
- Chunk overlap complicates positions and recombination (Onyx sets it to zero; RAGFlow has a highlight bug).
- Search backends get swapped (Danswer to Vespa, Onyx from Vespa to OpenSearch).
- Several 2024-era projects have stalled or relicensed: Verba is archived, R2R has had no pushes for about a year, and Morphik moved to BSL.

### Cited Findings
- RAGFlow says outright that its basic citation path is probabilistic ("guessing") and that the agentic path is made "deterministic" by having code extract echoed ids. It also notes a UI bug where markers placed inside code spans "would render as literal text and never become a clickable citation", and makes the rewrite idempotent — [citations.go](https://raw.githubusercontent.com/infiniflow/ragflow/main/internal/agentic_rag/citations.go)
- RAGFlow overlap bug: the chunker "drops overlap-head coordinates", so PDF highlights go missing or land in the wrong place — [Issue #18148](https://github.com/infiniflow/ragflow/issues/18148)
- Onyx: "Not supporting overlaps, we need a clean combination of chunks" — [chunker.py](https://raw.githubusercontent.com/onyx-dot-app/onyx/main/backend/onyx/indexing/chunker.py)
- Danswer chose Vespa for score normalization across vector and keyword search and for multipass indexing — [ZenML case study](https://www.zenml.io/llmops-database/scaling-enterprise-rag-with-advanced-vector-search-migration). The current tree uses OpenSearch, with Vespa code left only in migrations and EE tasks — [repo tree](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/document_index)
- Kotaemon's `CiteEvidence` limits ("max 15 words", "maximum 5") are prompt-only and not validated. Parse failures are printed and return `None`, and `ainvoke` raises `NotImplementedError` — [citation.py](https://raw.githubusercontent.com/Cinnamon/kotaemon/main/libs/kotaemon/kotaemon/indices/qa/citation.py)
- PaperQA2: using a weaker model for RCS hurt accuracy below having no RCS at all — [arXiv 2409.13740](https://arxiv.org/html/2409.13740)
- Verba was archived 2026-06-08 with no further fixes — [README](https://github.com/weaviate/Verba). R2R's last push was 2025-11-07 — [GitHub API](https://api.github.com/repos/SciPhi-AI/R2R)
- RAGFlow moved from Python to Go (about 3,210 Go files against 167 Python files, with old Python paths removed) — [repo tree](https://github.com/infiniflow/ragflow/tree/main/internal)

### Inferences
- Every surveyed system lets the model emit citation tokens freely and then cleans up. The robust pattern (RAGFlow agentic, Vault) is: code assigns ids, the model echoes them, code resolves them, and an unknown id is dropped, never "fixed". This independently backs Vault's `CITATION_UNRESOLVED` rule.
- Strategically, the frameworks (LlamaIndex, Haystack) give citation scaffolding only at the `[n]`-to-chunk level, so adopting them would not save Vault its hardest part. The citation layer is the right place for custom code. Chunking, fusion and reranking are commodity patterns to imitate rather than build differently.

### Gaps
- No formal post-mortem blog posts by these teams were found within the tool budget. The lessons above come from code comments, issues and repo status.
- GitHub discussions about Onyx's Vespa-to-OpenSearch rationale were not found.

## Q5. Licenses (for borrowing ideas or code)

### Takeaway
Permissive (code is borrowable with attribution): RAGFlow, Kotaemon, PaperQA, Haystack (all Apache-2.0); R2R, open-notebook, AnythingLLM, LlamaIndex (all MIT); Onyx Community Edition (MIT); Verba (BSD-3-Clause). Restrictive: Khoj (AGPL-3.0, so avoid copying code), Morphik (BSL 1.1, source-available, commercial limits) and Onyx's `ee/` directories (separately licensed).

### Cited Findings
- Apache-2.0: RAGFlow, Kotaemon, PaperQA, Haystack — [GitHub API ragflow](https://api.github.com/repos/infiniflow/ragflow); [kotaemon](https://api.github.com/repos/Cinnamon/kotaemon); [paper-qa](https://api.github.com/repos/Future-House/paper-qa); [haystack](https://api.github.com/repos/deepset-ai/haystack)
- MIT: R2R, open-notebook, AnythingLLM, LlamaIndex — [r2r](https://api.github.com/repos/SciPhi-AI/R2R); [open-notebook](https://api.github.com/repos/lfnovo/open-notebook); [anything-llm](https://api.github.com/repos/Mintplex-Labs/anything-llm); [llama_index](https://api.github.com/repos/run-llama/llama_index)
- Onyx: MIT Community Edition, with a separately licensed Enterprise Edition (the API reports SPDX NOASSERTION, consistent with mixed licensing). EE code sits under `backend/ee/` — [README](https://github.com/onyx-dot-app/onyx); [repo tree](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee)
- Khoj: AGPL-3.0 — [GitHub API](https://api.github.com/repos/khoj-ai/khoj)
- Morphik: Business Source License 1.1. It is free for personal use and for commercial production under US$2,000/month gross revenue, and each version converts to Apache 2.0 four years after release. The repo blurb still says "Open-source", which conflicts with the license — [README](https://github.com/morphik-org/morphik-core)
- Verba: BSD-3-Clause, archived — [README](https://github.com/weaviate/Verba)

### Inferences
- For a take-home, copying ideas (not code) is safe from all of them. If any code is lifted, for example Onyx's streaming citation parser or Kotaemon's PDF.js highlighter, take it from the MIT or Apache files only, keep the license header, and avoid Onyx `ee/`, Khoj and Morphik.

### Gaps
- The exact license file for Onyx `backend/ee/` was not read.
