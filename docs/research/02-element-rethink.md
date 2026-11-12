# Pass 2 — Element-by-element rethink, failure modes, explainability

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.
> **Provider note (2026-11-13):** The A10 section below (Anthropic `search_result` blocks, `citations_delta`, Anthropic caching and 429 rows) is superseded by design §8.5 (application-managed citations) and §4 (OpenAI roles); model names map to roles `fast`/`balanced`.

Tags: **[V]** verified this session (docs/repo checked) · **[C]** believed, verify at build · **[U]** unknown until measured.

---

## Part A — Each element: options → decision

### A1. Upload & storage
| Option | Verdict |
|---|---|
| Read whole file into memory, then hash | Simple; fine at 25 MB but wastes RAM under concurrency |
| **Stream to temp file, hash while writing, then atomic move** | **Chosen.** Constant memory; hash is ready when upload ends |
| Type detection by extension / `Content-Type` | Rejected (spoofable) |
| **Magic-byte sniffing** (`filetype`, pure Python, MIT [C]) + per-type parser validation | **Chosen** |

Storage is **content-addressed**: `blobs/{sha256}`, behind an `ObjectStore` interface (`put/get/delete/url`). Local volume by default, MinIO optional. Content addressing gives dedupe and idempotent writes for free: re-uploading the same bytes overwrites the same key with the same content. Deletion is reference-counted: a blob is removed only when no version references it.

### A2. Extraction
| Option | License | Strength | Weakness |
|---|---|---|---|
| **pdfplumber** | MIT [V] | Per-char/word boxes (`x0, top, x1, bottom`) [V]; table finder; pure Python | Slower; reading order can break on multi-column |
| PyMuPDF | AGPL [V] | Fast; good reading order | License |
| pypdfium2 | (no SPDX on GitHub [V]; Apache/BSD [C]) | Fast; good word order in one study | Thinner table support |
| **Docling** | MIT [V], 70k★, active [V] | Layout model, reading order, table structure, element provenance | Heavy (ML models), slower per page [U] |

**Decision: two tiers.**
- **Fast path, pdfplumber (S1):** words with boxes → lines → blocks. A heading heuristic uses font size and weight. Tables come from `find_tables` and are serialized to markdown, with the header row repeated on every split.
- **Quality score per page:** text density, a multi-column signal (bimodal x0 clustering), table count, and the ratio of replacement characters.
- **High-fidelity path, Docling (stretch, eval-gated):** pages that fail the quality score are re-extracted on a separate `ingest-hifi` queue. We take Docling only if the eval shows a gain on our corpus.
- **Scanned pages** (near-zero text): `needs_ocr`. Optionally, Claude vision transcribes the page image. That output has no word boxes, so highlights for those pages fall back to page level, and the UI shows that honestly.

DOCX: python-docx, with headings from paragraph styles. There are no pages, so the location is `section_path + paragraph index`, and highlights render in a text view.

### A3. Chunking
| Option | Verdict |
|---|---|
| Fixed-size token windows | Rejected. This is the most-cited production mistake. |
| Semantic chunking (embedding breakpoints) | Expensive at ingest; no consistent evidence of benefit [U] |
| **Structure-aware: split at heading/section, then by paragraph to a ~400-token target (max 800), tables whole** | **Chosen** |
| Small-to-big / parent expansion | **Chosen, cheaply.** Retrieve at chunk level, then add the adjacent same-section chunk while the evidence budget allows. This addresses "the answer was split across two chunks". |

Inside each chunk we store **sentence offsets**. They become the citation unit (A10).
Chunking config is versioned (`chunker@v1`). A change triggers a re-chunk job and never mixes versions inside one document version.

### A4. Contextual prefixes
- **Structural prefix** (deterministic, free): always on.
- **LLM contextual prefix** (Haiku, document cached): its value is measured in the S2 ablation before it's enabled. Cost at Haiku prices is small [V prices]. The real cost is ingest latency, so it runs after READY as an upgrade: the document is chat-able first, and the index is improved in place afterwards.

### A5. Embeddings
- OpenAI `text-embedding-3-small`. The default is 1536 dimensions, and shortening via `dimensions` is supported [C].
- Size check: 1000 documents × ~100 chunks × 1536 × 4 bytes ≈ 0.6 GB of vectors, which is fine for Postgres. We keep 1536. 512 is an option to evaluate if memory pressure ever matters.
- **Query embeddings are cached** by normalized query text (Redis, 7 days). Repeat and suggested questions then skip the API call.
- Failure behavior: an embedding API outage blocks ingestion (it retries with backoff) and blocks *new* queries. If the query embedding call fails, retrieval falls back to **lexical-only**, marked `degraded=lexical_only` in the run and shown to the user as "keyword search only".

### A6. Vector index
> **Superseded by pass 4 §3 / design §7.2:** exact search within the owner's scope is the default; HNSW is only the large-scope escape hatch. The filtering gotcha below still applies to that escape hatch.

- HNSW with cosine distance.
- **Critical gotcha:** filtered HNSW queries can return too few rows. Owner and scope filters are applied after the index scan, and the default `ef_search` is 40. pgvector 0.8 added iterative index scans (`hnsw.iterative_scan`), which are off by default [C]. **Decision:** require pgvector ≥ 0.8, set `hnsw.iterative_scan = relaxed_order` per session, and assert in a test that a filtered query returns k rows.
- For small scopes (one or a few documents, fewer than ~20k chunks), **use exact search**: order the scoped rows by distance. It's faster and has perfect recall. The planner decides; we verify with `EXPLAIN` in a test on realistic volumes, because tiny tables skip the index and hide bugs.

### A7. Lexical search
- Postgres FTS: `websearch_to_tsquery('english', q)` plus `ts_rank_cd`, using a GIN index on a `tsvector` built from `embedding_text`.
- **Identifier problem:** clause numbers, IDs and amounts (`4.2(b)`, `INV-2291`, `$15,000`) are mangled by the english stemmer. We add a second `simple`-config tsvector over identifier-like tokens, and boost exact-identifier hits. We call this "lexical", not BM25. Real BM25 (ParadeDB) is deferred unless the eval shows a gap.

### A8. Fusion and rerank
- RRF with k=60 [C standard]. Per-document quota for compare.
- Reranker options:

| Option | Latency on CPU, 40 pairs [U] | Quality [U] | License |
|---|---|---|---|
| `ms-marco-MiniLM-L-6-v2` (fastembed) | low | ok | Apache [C] |
| `bge-reranker-base` (fastembed) | higher, model ~1 GB | better | MIT [C] |
| Haiku listwise rerank | ~0.5–1 s, costs tokens | good | API |

**Decision:** MiniLM by default, swapped only if the eval shows a gain worth the latency. **The reranker never runs on the API event loop.** CPU inference would block every other request. It runs in a small `reranker` service (same image, separate container, bounded concurrency), so it scales independently. This one decision matters most for the 100-concurrent-user target.

### A9. Query analysis and evidence gate (the LLM judgment points)
- `analyze_query` (Haiku, structured): always runs. A speculative hybrid retrieval on the raw query runs in parallel, and we reuse it when the standalone query equals the raw query.
- **The gate is tiered, so the common case stays fast:**
  - Single-dimension lookup whose top rerank score is in the high band: **skip the LLM gate**, coverage = supported (reason `GATE_SKIPPED_HIGH_SCORE`).
  - Low band with no candidates above the floor: **abstain without an LLM call** (`ABSTAIN_NO_EVIDENCE`).
  - Middle band, or compare / multi-dimension: run the **LLM gate** to get the coverage matrix and conflict flags.
  - Band thresholds are calibrated on the golden set; calibration is part of the S4 gate.

### A10. Generation and citations: switch to `search_result` blocks
Anthropic has **`search_result` content blocks**, built for RAG. Each one has `source`, `title` and an array of text blocks. Citations come back as `search_result_location` with the result index and start/end block index, and no beta header is required [V: docs]. Streaming delivers `citations_delta` [V: SDK types].

**Decision:**
- Each evidence chunk becomes **one `search_result`**:
  - `source = "vault://chunk/{chunk_id}"`
  - `title = "{doc title} v{n} · p.{page} · {section}"`
  - **each sentence of the chunk is its own text block**
- Citations are then **sentence-precise**. `start/end_block_index` maps directly to sentence offsets, then to character spans, then to word boxes, then to the highlight. The fuzzy text matching we planned in pass 1 is no longer needed in most cases.
- The manifest stays, as defense in depth, but `source` already carries the chunk ID.
- Full-context mode uses the same format with every chunk of the document in reading order, so one resolver serves both modes.
- Prompt caching: a breakpoint after the system prompt. In full-context mode, a second breakpoint after the evidence.

Answer-format failure to design around: **markdown tables don't mix well with citations**, because citations split text into blocks and a table would be fragmented. **Decision:** the model never writes tables. For compare, it writes one short cited paragraph per dimension per document. The **UI renders the comparison table** from the coverage matrix plus segments. That gives a better table and intact citations.

### A11. Streaming transport
- SSE via `sse-starlette` [C], with a heartbeat comment every 15 s and `X-Accel-Buffering: no`.
- **Reconnect strategy:** the server keeps generating after a disconnect and persists the result. On reconnect, the client calls `GET /v1/runs/{run_id}`, which returns status plus partial or full content. There is no event-replay buffer to build.
- WebSocket is only worth it for the matrix and processing views, where many cells or documents push updates. Decision: SSE everywhere first; the matrix also uses SSE, one stream per matrix.

### A12. Memory
No change, except:
- the rolling summary also records **resolved entities** ("the agreement" = doc X), which feed `analyze_query`
- a summary failure never blocks a turn; the working window just shrinks

### A13. Caching
| Cache | Key | Safe? |
|---|---|---|
| Query embedding | normalized query text + model | Yes |
| Retrieval results | standalone query + sorted version IDs + retrieval config version | Yes. Invalidated by construction when versions change. |
| Full answer | — | **No.** Answers depend on history, and stale answers are a top complaint. |
| Brief / summaries | version ID + params + prompt version | Yes |
| Anthropic prompt cache | provider-side | Yes; monitored via `cache_read_tokens` |

### A14. Auth
API keys for API clients. For the frontend, an httpOnly session cookie set by a login endpoint, with the same user model. Every repository method takes `owner_id` explicitly, so it can't be forgotten. A lint rule or test checks that every query on owned tables filters by owner.

---

## Part B — Failure modes (FMEA-style)

Severity (S): 3 = wrong answer or data exposure · 2 = feature broken · 1 = degraded. Detect = how we notice. Surface = what the user sees.

### B1. Ingestion
| Failure | S | Detect | Mitigation | Surface |
|---|---|---|---|---|
| Spoofed or unsupported type | 2 | Magic bytes | Reject `415 unsupported_type` | Clear error at upload |
| Encrypted PDF | 2 | Parser exception class | `unsupported/encrypted` | "Password-protected PDFs aren't supported" |
| Decompression bomb (DOCX zip, PDF streams) | 2 | Size and ratio limits, parser timeout, worker memory limit | Kill, mark failed | "File couldn't be processed safely" |
| Scanned / empty pages | 3 if silent | Per-page text density | `needs_ocr` per page; OCR fallback | Page badges "image only" |
| Multi-column mis-ordering | 3 (garbled evidence) | Column signal in quality score | Hi-fi re-extract (stretch) | Quality badge on the document |
| Table cells shifted or flattened | 3 | Table quality heuristics, eval questions on tables | Header repetition; hi-fi path | — |
| Worker crash mid-stage | 1 | Lease expiry | Sweeper + idempotent stage | Timeline shows "retrying" |
| Enqueue lost after commit | 1 | Sweeper (undispatched > grace) | Re-dispatch | Delay only |
| Duplicate execution (late ack) | 1 | — | Idempotent stage writes, unique keys | — |
| Embedding API down | 2 | Gateway errors / circuit breaker | Backoff; version stays `processing`, retried | "Indexing delayed" |
| Poison document (always fails) | 2 | attempt_count ≥ max | `failed` + dead-letter state, no infinite retry | Error with reason and retry button |
| Delete during processing | 1 | `deleted_at` check between stages | Abort and clean up | — |
| Re-upload of a new version while v1 is processing | 1 | — | Versions independent; latest READY wins for new chats | Version badge |

### B2. Retrieval
| Failure | S | Detect | Mitigation | Surface |
|---|---|---|---|---|
| Filtered HNSW returns too few rows | 3 (silent recall loss) | Test asserting k results; `candidates_returned` metric | Iterative scan / exact search for small scope | — |
| Identifier queries miss | 3 | Eval set with identifier questions | `simple` tsvector + exact boost | — |
| Answer split across chunks | 3 | Eval "incomplete" | Neighbor expansion | — |
| Embedding/model mismatch after migration | 3 | `embedding_model` filter; count check | Reindex job; query filters by model | — |
| Reranker service down or slow | 2 | Health check, timeout | Fall back to RRF order, `degraded=no_rerank` | "Reduced ranking quality" in details |
| Query embedding fails | 2 | Gateway | Lexical-only | "Keyword search only" |

### B3. Chat orchestration
| Failure | S | Detect | Mitigation | Surface |
|---|---|---|---|---|
| Hallucinated / unsupported claim | 3 | Gate, citations, optional support check, eval | Abstain or partial; uncited segment metric | Coverage state, dotted underline |
| Conflicting sources merged | 3 | Gate `conflict` flag | "Sources disagree" template | Callout with both quotes |
| Misread follow-up ("it", "that clause") | 3 | Eval multi-turn | Rewrite plus resolved entities | Standalone query shown in "details" |
| Wrong document targeted | 3 | `target_document_ids` validated ⊆ scope | Clarify route when ambiguous | Clarification chips |
| Prompt injection in a document | 2 | (hard to detect) | Docs are data blocks; no tools; system rule | Citations make it visible |
| Budget or deadline exceeded | 2 | Harness | Graceful partial: answer with the evidence so far, or abstain | Reason shown |
| Anthropic 429 / 529 / timeout | 2 | Typed errors, breaker | Retry with jitter; then 503 retryable | "Temporarily unavailable, retry" |
| `refusal` / `max_tokens` stop | 2 | `stop_reason` check | Typed error / continuation | Explicit message |
| Citation resolves to an unknown block | 3 | Resolver validation | Drop citation, mark segment uncited, log `citation_unresolved` | — |
| Concurrent turns in one session | 1 | CAS | 409 | "Wait for the current answer" |
| Client disconnect | 1 | — | Continue + persist | Reconnect shows the result |
| Long-context scope too large | 2 | Token count before call | Force RAG, reason `FULL_CONTEXT_TOO_LARGE` | Mode shown |

### B4. Platform
| Failure | S | Detect | Mitigation |
|---|---|---|---|
| Redis down | 2 | `/readyz` | Uploads 503; chat continues, limiter fails open (alert); caches miss |
| Postgres down | 3 | `/readyz` | API 503; workers pause (Celery retries) |
| DB pool exhaustion | 2 | Pool wait metric | No connection across streams; pool sizing (pass 4) |
| Disk full (blobs) | 2 | Disk metric alert | Upload 507, alert |
| Cost runaway | 2 | Cost/hour metric, per-user quota | 429 quota |
| Model or prompt change regresses quality | 3 | Eval required in CI for prompt/model changes | Pinned per run; rollback via version |

---

## Part C — Explainability, by audience

The core idea: **explanations are generated from structured reason codes by deterministic templates, not by asking the LLM to explain itself.** An LLM's self-explanation is another unverifiable generation. Our explanations come from the ledger.

### C1. Reason-code taxonomy (examples)
`MODE_RAG_DEFAULT` · `MODE_FULL_CONTEXT_WHOLE_DOC_INTENT` · `FULL_CONTEXT_TOO_LARGE` · `GATE_SKIPPED_HIGH_SCORE` · `GATE_PARTIAL_COVERAGE` · `RETRY_LOW_COVERAGE` · `ABSTAIN_NO_EVIDENCE` · `ABSTAIN_AFTER_RETRY` · `CONFLICT_DETECTED` · `CLARIFY_AMBIGUOUS_DOCUMENT` · `DEGRADED_LEXICAL_ONLY` · `DEGRADED_NO_RERANK` · `CITATION_UNRESOLVED` · `ANSWER_STALE_VERSION`

Each code has a user template ("I searched 3 documents but found nothing about relocation. The closest sections were…") and an operator description.

### C2. User view (plain language, always available)
- **Inline citations** at sentence precision. Hover shows the quote; click opens the original page with the exact sentence highlighted.
- **Answer footer:** "From 6 passages in 2 documents · Hybrid search · Supported" with actions to show passages and to search deeper.
- **Passages panel:** used passages, and retrieved-but-unused passages greyed out.
- **Coverage:** the per-dimension matrix for compare, plus a "Sources disagree" callout.
- **Abstention that explains itself:** what was searched, the closest sections, and suggested rephrasings.
- **Staleness banner** when a cited version is superseded.
- **Document trust signals:** extraction quality badge, pages needing OCR, verified vs unverified brief facts.

### C3. Operator view (`?detail=internal`, internal UI tab)
- **Waterfall** of nodes with durations (from `run_steps`), LLM calls with tokens, cache hits and cost.
- **Candidates table:** vector rank/score, lexical rank/score, RRF, rerank, selected, cited. Sortable, so "why wasn't X used" can be answered.
- Prompt IDs and versions, model, effort, limits, budget used, reason codes, degraded flags.
- Trace ID linking to the OTel trace (pass 3).

### C4. Evaluator / developer view
- **Replay:** `POST /v1/runs/{id}/replay {override: {prompt_version?, answer_model?, mode?, retrieval_config?}}` re-executes with the **frozen evidence** (or fresh retrieval) and returns a diff. This isolates retrieval problems from generation problems, the core RAG debugging question.
- **Counterfactual "search deeper":** the same question in full-context mode, compared side by side. This is the user feature and the eval tool at once.
- Eval reports: per-question pass/fail across configs, ablation tables, a regression list against the last baseline.

---

---

## Sources
- [pgvector 0.8.0 release notes (Nile)](https://www.thenile.dev/blog/pgvector-080)
- [pgEdge — pgvector filtering](https://docs.pgedge.com/pgvector/v0-8-2/filtering/)
- [AWS — pgvector 0.8.0 on Aurora](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql)
- [Crunchy Data — hybrid search with pgvector](https://crunchydata.com/blog/hybrid-vector-search)
- [Claude docs — search results](https://platform.claude.com/docs/en/build-with-claude/search-results)
- [Claude docs — Messages API (Kotlin types incl. citations_delta)](https://platform.claude.com/docs/en/api/kotlin/messages)
- [Vercel — text-embedding-3-small](https://vercel.com/ai-gateway/models/text-embedding-3-small)
- [OpenAI embeddings guide (mirror)](https://openai.cadn.net.cn/docs_en/guides_embeddings)
- [Table extraction benchmark](https://soricm.github.io/table-extraction-benchmark/)
- [pdfplumber vs PyMuPDF — licensing](https://pdf.oxide.fyi/docs/comparison/pdfplumber-vs-pymupdf)
- [Comparative study of PDF parsers](https://www.themoonlight.io/en/review/a-comparative-study-of-pdf-parsing-tools-across-diverse-document-categories)
