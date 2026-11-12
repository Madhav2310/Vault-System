# Pass 1 — What users of document-AI tools want, what they hate, what builders get wrong

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.

Evidence quality: mostly reviews, practitioner blogs, GitHub issues and one peer-reviewed paper. Treat individual claims as **signals**, not statistics. Sources at bottom.

---

## 1. The landscape

| Product | Shape | Loved for | Complained about |
|---|---|---|---|
| NotebookLM | Notebooks of sources, chat, notes, audio overviews | Citations are the core value; grounded-only answers | Misattribution between sources / fabricated citations reported; source view strips original formatting; can't export answers with live citations; can't search across notebooks ("don't remember what's in each"); quality regressions after model updates |
| ChatPDF / Humata / AskYourPDF | Single-PDF chat | Fast, simple | **Right document, wrong page/passage**; highlight not matching the claim; total hallucinations; weak on cross-section questions in long reports; scanned PDFs unreadable |
| Adobe Acrobat AI Assistant | Chat inside the PDF viewer, multi-file | Citations with title/section/page, in-viewer | Still requires verification; usage caps; weak beyond the docs |
| Hebbia Matrix | **Grid: rows = documents, columns = questions**, every cell cited | Comparison at scale, each cell links to exact passage | Learning curve for building columns/workflows; "trust the output" fails for high stakes |
| Open WebUI / AnythingLLM (self-hosted) | Generic RAG chat | Free, local | Aggressive context trimming → wrong answers that full-context mode fixes (Open WebUI #10747); inconsistent answers API vs UI; "superficial, rigid" RAG |

## 2. Recurring user pain → our response

| # | Pain (signal) | Root cause | Response in Vault |
|---|---|---|---|
| P1 | Citation points to the wrong page/passage | Page-level or chunk-level citation, model-written markers | Native citations at chunk-block level **plus exact-span highlighting on the rendered original page** (§3.1) |
| P2 | Fabricated / misattributed citations | Model invents references | Citations come from the API (`cited_text` is extracted from supplied blocks), resolved through the manifest; per-segment support check optional (§3.2) |
| P3 | Source view loses formatting | Showing extracted text, not the document | Render the **original page** (PDF.js) with highlight overlay; text view only as fallback (DOCX/TXT) |
| P4 | Can't export answers with citations | Citations are UI-only | Export answer/thread/matrix as Markdown/DOCX with footnotes + deep links to `/chunks/{id}` |
| P5 | Can't search across notebooks / forgets what's where | Siloed scopes | Vault-wide scope by default, visible scope chips, a library of brief cards |
| P6 | Wrong answers from trimmed context; full context fixes it | Over-aggressive retrieval | Long-context mode + a user-visible "**Search deeper**" escalation (RAG → full document) |
| P7 | Inconsistent answers to the same question | Non-pinned prompts/models, hidden state | Run ledger pins prompt version, model, evidence; "regenerate" shows what changed |
| P8 | Stale / superseded documents cited | No versioning | Versions + **stale-answer flags** when a cited version is superseded (§3.5) |
| P9 | Contradictory sources silently merged | Generator averages conflicting evidence | Gate detects conflict → answer shows "**Sources disagree**" with both quotes (§3.3) |
| P10 | Tables come out as number soup | Flattened grids, headers lost across pages | Table-aware extraction, tables as standalone chunks with header row repeated on every split; multi-page header propagation |
| P11 | Scanned / image PDFs silently fail | Text-only extraction | Detect per page; `needs_ocr` surfaced in UI; vision fallback via Claude PDF/image input for flagged pages (stretch) |
| P12 | Confident wrong answers erode trust; users quietly leave | No abstention, no feedback | Coverage states, honest abstention with "what I searched", per-citation feedback → becomes eval cases (§3.6) |
| P13 | Long outputs that bury the answer / wrong format | Generic prompt | Format policy: answer-first sentence, then support; comparisons → table; lookups → one line + quote |

## 3. Things that make it feel modern rather than "chatbot over PDFs"

### 3.1 Exact-span highlighting on the original page (highest trust impact)
- At extraction, pdfplumber gives word boxes per page (`x0, x1, top, bottom`). Store per chunk a list of `(page, char_start, char_end)` spans and a per-page word-box index.
- Citations are sentence-precise (each sentence is its own text block in a `search_result`, see pass 2 §A10), so a citation maps directly to sentence offsets → char range → word boxes → rectangles. Text matching on `cited_text` is only a fallback.
- UI: hover a citation → quote preview; click → original page opens scrolled to a yellow highlight of *exactly* that span.
- Failure mode: offsets drift (ligatures, hyphenation). Fallback order: exact span → normalized fuzzy match → whole-chunk box → page only. Log which level was used (metric: `highlight_precision_level`).

### 3.2 Claim-level support badges
Each answer segment carries its evidence. Optional cheap Haiku entailment check per segment ("does the quote support this sentence?") → `supported | weak`. Weak segments rendered with a dotted underline. Runs async after the stream ends so it adds no latency; result arrives as an SSE `verification` event. Measured in eval against human labels before being shown to users.

### 3.3 "Sources disagree"
`evidence_gate` returns, per dimension, `conflict: bool` plus the conflicting chunk IDs. Generator instruction: when a conflict is flagged, present both positions with citations instead of choosing. Surfaced as a distinct UI callout. Typical cases: two versions, two contracts, a policy vs an amendment.

### 3.4 Compare Matrix (Hebbia-style grid, done lightweight)
- Rows = documents, columns = questions; each cell = a mini-run (scoped to one doc) with citations and coverage state.
- Columns are **auto-suggested from the DocumentBrief schema** (parties, term, notice period, payment, termination…), which addresses Hebbia's learning-curve complaint.
- Execution: Celery fan-out, background lane, cells stream in as they finish (SSE/WebSocket); each cell is idempotent by `(matrix_id, doc_version_id, column_id, column_version)`.
- Shows off async processing, rate limiting and the ledger at once. Exportable as CSV/XLSX with citations.

### 3.5 Living answers: staleness and change awareness
- When a new version of a document becomes READY, find past runs whose `run_versions` include the old version → mark their messages `stale` with a banner and a "re-ask on v2" button.
- Later (stretch): section-level diff between versions → "what changed" brief.

### 3.6 Feedback that improves the system
- Thumbs on answers; **"wrong citation"** and "missing info" on individual citations/segments.
- Each flagged item is turned into an eval candidate (`eval/inbox/`), reviewed, promoted into the golden set. This closes the loop the Seven Failure Points paper argues for (robustness is only validated in operation).

### 3.7 Proactive document intelligence
- On READY: brief card (type, parties, key dates, obligations, unusual clauses) with verified quotes, plus 4–6 suggested questions specific to the doc.
- Vault-level views built from briefs: **key dates timeline** across documents, "documents with auto-renewal", etc. Answered from structured data (meta route), not RAG.

### 3.8 Transparent retrieval in the user view (not just the debug trace)
Under every answer: "Answered from **8 passages in 2 documents** · RAG mode · Search deeper". Expanding shows the passages that were used, plus passages that were retrieved but not used (greyed out). Users learn what the system saw. This directly counters the "it didn't look at my document" complaint.

### 3.9 Smaller things that feel modern
Drag-a-folder batch upload with per-file timelines · progressive READY (chat before enrichment finishes) · scope chips with doc counts · streaming status steps · keyboard-first command bar (⌘K: jump to doc, ask across vault) · copy answer with citations · share a read-only run link.

## 4. What builders commonly get wrong → our guardrail

| Mistake (practitioner consensus) | Guardrail in our design |
|---|---|
| Naive fixed-size chunking | Structure-aware chunking, tables kept whole, structural prefixes; chunking config versioned |
| Vector-only retrieval | Hybrid vector + lexical with RRF |
| No reranking / context stuffing | Cross-encoder rerank, evidence token budget |
| Over-trimming context | Long-context mode + "Search deeper" |
| No eval, or eval only on self-written questions (practitioners report 90% offline → 60% in production) | Third-party corpus, unanswerable questions, production feedback → golden set |
| Ignoring tables, scans, multi-column layout | Table-aware chunks, per-page OCR detection, reading-order check |
| Stale/duplicate docs | sha256 dedupe, versions, staleness flags |
| Permissions bolted on after | Scope computed by code and applied in SQL, negative tests |
| No observability | Run ledger + OTel + dashboards (pass 3) |
| Silent model/prompt changes cause regressions (NotebookLM reports) | Prompt + model pinned per run; eval required for changes |

The Seven Failure Points paper (Barnett et al., CAIN 2024) maps onto our components:
missing content → abstention + "what I searched" · missed top-ranked docs → hybrid + rerank + recall@k eval · not in context → evidence budget + full-context escalation · not extracted → citation-required generation · wrong format → format policy · incorrect specificity → `dimensions` in query analysis · incomplete → coverage matrix per dimension.

## 5. Prioritization (power law)

| Tier | Feature | Value | Cost | Notes |
|---|---|---|---|---|
| **A — defines the product** | Exact-span highlight on original page | Very high (P1, P3) | Medium | Needs bbox capture in S1 extraction; must be designed in now |
| A | Coverage + honest abstention + "sources disagree" | Very high (P9, P12) | Low (gate already exists) | |
| A | Transparent "answered from N passages · Search deeper" | High (P6) | Low | |
| **B — strong differentiators** | Compare Matrix | High | Medium | Reuses pipeline; a new slice |
| B | Brief cards + suggested questions + dates timeline | High | Low–medium | Already S6 |
| B | Stale-answer flags on new versions | Medium–high | Low | Data already in `run_versions` |
| B | Per-citation feedback → eval inbox | Medium (compounding) | Low | |
| **C — polish / stretch** | Export with citations | Medium | Low | |
| C | Claim support badges | Medium | Low–medium | Only after eval shows the checker is accurate |
| C | Vision fallback for scanned/chart pages | Medium | Medium | Cost per page; batch API |
| C | Version diff "what changed" | Medium | Medium–high | |

Scope risk: the spec rewards "fully functional subset over partially implemented features". Tier A must be finished before anything in B starts; B items are each a separate gated slice.

---

## Sources
- [Enterprise DNA — practitioner NotebookLM review](https://enterprisedna.co/resources/blog/practitioner-notebooklm-review)
- [Superlore — NotebookLM limits and workarounds](https://superlore.ai/blog/notebooklm-limits-workarounds)
- [Google AI forum — Gemini 3.1 Pro update broke NotebookLM grounding](https://discuss.ai.google.dev/t/critical-regression-gemini-3-1-pro-update-feb-19-completely-broke-notebooklm-s-rag-grounding/126857)
- [RoboRhythms — NotebookLM for document research](https://www.roborhythms.com/notebooklm-for-document-research/)
- [G2 — NotebookLM reviews](https://www.g2.com/products/google-notebooklm/reviews_and_filters)
- [Junaid Mubeen — gained and lost from chatting with documents](https://junaidmubeen.substack.com/p/what-ive-gained-and-lost-from-chatting)
- [Smallpdf — AI document accuracy study](https://smallpdf.com/blog/ai-document-accuracy-study)
- [PDFgear — Bing vs ChatPDF vs Humata](https://www.pdfgear.com/insights/compare-the-new-bing-chatpdf-humata-and-pdfgear.htm)
- [Educators Technology — Acrobat AI Assistant review](https://www.educatorstechnology.com/2026/10/adobe-acrobat-ai-assistant-review.html)
- [Adobe — view citations](https://helpx.adobe.com/bg/acrobat/using/view-citations.html)
- [Contrary Research — Hebbia](https://research.contrary.com/company/hebbia)
- [G2 — Hebbia reviews](https://www.g2.com/products/hebbia)
- [Open WebUI issue #10747](https://github.com/open-webui/open-webui/issues/10747)
- [Open WebUI RAG troubleshooting](https://docs.openwebui.com/troubleshooting/rag.md)
- [AnythingLLM issue #2526](https://github.com/Mintplex-Labs/anything-llm/issues/2526)
- [Medium — AnythingLLM RAG superficial](https://medium.com/@pamperherself/hands-on-experience-with-anythingllm-rag-tools-seem-superficial-and-too-rigid-e904e3ceab4c)
- [Barnett et al. — Seven Failure Points (arXiv 2401.05856)](https://arxiv.org/abs/2401.05856v1)
- [Enterprise DNA — RAG in production](https://enterprisedna.co/resources/blog/practitioner-rag-in-production)
- [DEV — don't build that RAG knowledge base](https://dev.to/chen115y/dont-build-that-rag-knowledge-base-seven-reasons-it-will-fail-and-what-to-build-instead-2c3g)
- [Towards Data Science — tables in PDFs for RAG](https://towardsdatascience.com/tables-in-pdfs-for-rag-dont-flatten-the-grid/)
- [DEV — layout-aware chunking](https://dev.to/gabrielanhaia/pdf-rag-is-where-most-pipelines-die-layout-aware-chunking-is-the-unlock-3go2)
- [Ertas — multi-column and scanned PDFs](https://www.ertas.ai/blog/pdf-to-rag-pipeline-multi-column-scanned)
