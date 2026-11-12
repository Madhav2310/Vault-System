# Pass 5 — The PDF geometry chain (citation → exact highlight)

> Rationale record. `docs/design.md` is authoritative; where this file differs, the design wins.

The Tier A feature "click a citation → see the exact sentence highlighted on the original page" depends on five links:
**extraction → sentence offsets → page spans → glyph boxes → screen rectangles.** Each link can fail silently. This pass collects how other systems solve it, records failures **reproduced on our own corpus**, and specifies a chain whose correctness is checked mechanically.

Tags: **[V]** reproduced or confirmed this session · **[C]** from docs/sources, verify at build · **[U]** unknown.

---

## 1. How others do it

| System | Granularity stored | Mechanism | Lesson for us |
|---|---|---|---|
| RAGFlow (94k★, Apache-2.0) | Per chunk: list of `[page, x0, x1, top, bottom]` 5-tuples (`position_int`) [V: source code comments] | Boxes stored next to the chunk; the frontend draws boxes for the cited chunk | Chunk-level only: highlights a whole chunk, not the cited sentence |
| Docling (70k★, MIT) | Per document item: `prov = {page_no, bbox, charspan}` [C: docs] | Official "visual grounding" example converts bbox to top-left origin and normalizes to page size before drawing | Store a char span next to the bbox. Normalize coordinates. Origin conversion is the classic bug. |
| LlamaIndex LiteParse | Per text item bbox in PDF points + page screenshots [C] | Guide warns that boxes are in points while screenshots are in pixels: scale by DPI/72 | Never mix coordinate units; store normalized [0,1] |
| OpenBB citations | pdfplumber `x0, top, x1, bottom` per span + page [C] | Backend returns rects per citation; frontend overlays | Same library as us; confirms the approach |
| Tensorlake | Bounding boxes per fragment, **kept separate from chunk text** (~10–15% storage overhead) [C] | Lightweight anchors in text, spatial metadata stored aside | Our layout file per version is the same idea |
| react-pdf-highlighter-extended (MIT) | Highlights stored in viewport-independent scaled coordinates [C] | Overlay components on PDF.js pages | Frontend option; store normalized rects so zoom never breaks highlights |
| PDF.js text-layer search (many apps) | None server-side | Concatenate `textContent.items`, search the quote, map back to spans. Must handle `hasEOL`, whitespace items, and quotes that don't match exactly [C] | Useful as a **client-side fallback**, too fragile as the primary mechanism |
| SciSpace citation highlights | Box when available | Fallback chain: bounding box → text search within the page → page only [C] | We adopt the same ladder, with the level made visible to the user |
| Anthropic citations | The citable unit is a text block; "split content into smaller blocks for finer citations"; PDFs are chunked into sentences internally [V: docs] | — | Validates sentence-level blocks as the unit, so a citation maps directly to a sentence range |

**Consensus:** store per-glyph or per-word geometry at extraction time, keep a character-offset mapping from evidence text to that geometry, normalize coordinates, and keep a fallback ladder. **Where we go further:** sentence-precise citations (one `search_result` text block per sentence) plus a mechanical round-trip check over the whole corpus.

---

## 2. Failure modes reproduced on our corpus (this session)

| # | Failure | Evidence | Impact if unhandled |
|---|---|---|---|
| F1 | **Word merging in justified two-column text**: default `x_tolerance` joins words ("atherreal-timetweetsfrom") | QASPER 1910.03814 [V] | Garbled sentences; keyword search misses; quotes don't match |
| F2 | **Column interleaving**: naive layout order reads across both columns | Same paper: 0/8 gold quotes located with naive extraction [V] | Sentences mix two columns; highlights jump between columns |
| F3 | **A fixed mid-page column split breaks single-column pages** | 1910.06592: 8/8 → 0/8 located with a mid-split [V] | A naive "fix" for F2 causes a worse regression |
| F4 | **Footnote injection**: footnote text appears inside a body sentence in content-stream order ("…ones 3https://gombru…") | 1910.03814 [V] | Sentence text includes foreign text; highlight spans the footnote |
| F5 | **CropBox offset**: pdfplumber word coordinates are relative to the MediaBox, while renderers (pdfium, PDF.js) draw the CropBox | Synthetic crop (50,50): pdfplumber word at x0=100, rendered page is 500×700 [V] | Every highlight shifted by the crop origin, silently |
| F6 | **Rotated pages**: pdfplumber reports coordinates in display (rotated) space, but text extraction on `/Rotate 90` pages scrambles word order ("nnal…", line 2 = "4.2(b).") | Synthetic rotate-90 [V] | Unreadable chunks; boxes need consistent orientation |
| F7 | **Ligatures**: single glyphs like "ﬁ" (U+FB01) expand to 2 characters under NFKC | 16–37 ligature glyphs per 8 pages in every paper [V] | Offsets drift by one character per ligature if text is normalized without a map |
| F8 | **Multi-character glyphs**: one glyph decodes to several characters | 5 in 1910.06592 [V] | Same offset drift as F7 |
| F9 | **Line-end hyphenation** ("multi-\nmodal") | up to 113 per 8 pages [V] | Exact quote matching and lexical search miss the hyphenated word |
| F10 | Duplicate glyphs (fake bold, shadows) | 0 in corpus [V]; common in the wild [C] | Doubled characters, doubled boxes |
| F11 | Invisible text (OCR layers, white text) | not in corpus [U] | Highlights on text the user can't see |
| F12 | Points vs pixels, origin conventions (PDF bottom-left vs top-left) | Docling and LiteParse docs [C] | Offset or flipped boxes |

---

## 3. The chain we build (design §6.4 / §8.5 amendments)

### 3.1 One character stream, built from glyphs (invariant I1)
Page text is **built by us from pdfplumber's character objects**, not taken from `extract_text()`. Every character of `page_text` carries a source:

```
page_text[i]  →  glyph_index | SYNTH_SPACE | SYNTH_NEWLINE
```

- Words come from `extract_words(use_text_flow=True, x_tolerance_ratio=0.15, return_chars=True)` on `page.dedupe_chars()`. This config was chosen by the extraction sweep: 99/100 expert quotes located vs 86/100 with defaults; papers 22/23 vs 9/23 (fixes F1 and F2).
- Ligature and multi-character glyphs (F7, F8) are expanded at build time (NFKC per glyph). **Every expanded character maps to the same glyph index**, so offsets never drift.
- Spaces and newlines between words are synthetic and map to no glyph. They never produce boxes.
- `original_text` of every chunk is an **exact slice** of `page_text`. No rewriting after this point.

### 3.2 Reading-order safety (F2–F4)
- Default: content-stream order (`use_text_flow=True`), validated by the sweep.
- **Column-jump detector:** for each page, if consecutive lines alternate between two x-clusters more than N times, the page is flagged `reading_order_suspect`. Those pages are re-ordered by column clustering (k-means on line x0 with k ∈ {1,2,3}, chosen by the silhouette score; no fixed mid-split, which avoids F3).
- **Footnote filter:** text lines whose font size is < 0.85 × the page's body-text median **and** that sit in the bottom 20% of the page are emitted as separate `footnote` elements, never merged into body sentences.
- The quality score (`pages.quality_score`) records which repairs were applied.

### 3.3 Coordinate normalization (F5, F6, F12)
All stored boxes are normalized to **the rendered page as the viewer shows it**: CropBox, `/Rotate` applied, top-left origin, values in [0, 1].

```
# pdfplumber char/word coords: points, top-left origin, relative to MediaBox, in display rotation
x0' = (x0 - crop.x0_from_left) / crop.width
y0' = (top - crop.top_from_top) / crop.height   # CropBox offset removed (F5)
… then clamp to [0,1]; drop boxes fully outside the CropBox
```

- Rotation (F6): extraction runs on a **de-rotated** view of the page, so word order is natural. The resulting boxes are then transformed by the page's rotation matrix into display orientation before normalization.
- Frontend: rect_px = rect_norm × rendered page size, for any zoom level.

### 3.4 Sentences, spans, rectangles
- Sentence offsets are computed on `original_text` (S1.19).
- `spans` map chunk offsets to `(page, page_char_start, page_char_end)`. Chunks never re-normalize text, so this map is the identity within a page.
- Highlight for a sentence range: collect glyph indexes for the characters in range → glyph boxes → **merge per line** (same baseline within tolerance, gap < 1.5 × average char width) → rectangles with 1-point padding.
- **Line-end hyphenation (F9):** `original_text` keeps the hyphen (evidence fidelity). `embedding_text` and the lexical `tsvector` use a dehyphenated copy. Citation matching never needs to dehyphenate, because citations index blocks, not text.

### 3.5 Fallback ladder (always labelled in the UI)
1. `sentence`: rectangles from glyph boxes (the target).
2. `chunk`: rectangles for the whole chunk (sentence map failed validation).
3. `text_search`: client-side PDF.js text-layer search for `cited_text` on the cited page (layout file missing or failed). Normalize both sides the same way; the match must be unique on the page.
4. `page`: scroll to the page only.

---

## 4. Validation: making the chain mechanically provable

| Check | What it proves | Scope | Threshold |
|---|---|---|---|
| **V1 Round-trip property** | For every sentence of every chunk: the characters under its rectangles, joined (whitespace-normalized), equal the sentence text | All corpus PDFs (313 pages incl. a 10-K and a 10-Q) | **[A] 100% on fixtures; [T] ≥ 99.5% of sentences on the corpus** |
| **V2 Containment** | Every rectangle lies within [0,1]² and overlaps ≥ 1 glyph box | All | [A] 100% |
| **V3 Fixture zoo** | Each failure mode F1–F12 has a fixture with an expected highlight snapshot | 14 fixtures (below) | [A] all pass |
| **V4 Visual QA by vision model** | Render the page (pypdfium2), draw the rectangles, crop the highlighted region, ask Claude Haiku 5.5 (vision) to transcribe the highlighted text; similarity to the sentence ≥ 0.9 | Random 100 sentences across corpus, stratified by doc type | [T] ≥ 97/100 |
| **V5 Client cross-check** | In e2e tests, PDF.js text-layer search for `cited_text` lands on the same page and overlaps the server rectangles (IoU ≥ 0.5) | Playwright, 30 citations | [T] ≥ 28/30 |
| **V6 Eval metric** | `highlight_precision`: share of PDF citations resolved at `sentence` level | Golden-set runs | [T] ≥ 90% (S3.15) |

**Fixture zoo** (`tests/fixtures/geometry/`, generated deterministically where synthetic):
single-column · two-column justified (QASPER 1910.03814 p.2) · two-column with footnote · page with CropBox offset · page rotated 90° · page rotated 270° · ligature-heavy paragraph · line-end hyphenation · fake-bold duplicate glyphs · invisible text layer (render mode 3) · table with merged cells (10-K) · sentence crossing a page break · superscript footnote markers · DOCX (text-view highlight).

---

## 5. What the user sees

- The highlight is drawn exactly over the cited sentence(s). It scales with zoom because coordinates are normalized.
- If precision isn't `sentence`, a small label ("approximate location" / "page only") says so. We never fake precision.
- Hover a citation to preview the quote; click to scroll to the highlighted region with a short pulse animation (reduced-motion respected).

---

## 6. Plan impact

- **S0 spike** (new S0.19–S0.22 in the plan): verify F5/F6 transforms, ligature mapping, a live `search_result` call with per-sentence blocks including streaming, and the partitioned `halfvec` + `btree_gin` DDL.
- **S1** gains geometry tasks: glyph-stream builder, column-jump detector and repair, footnote filter, coordinate normalization, de-rotation, line merging, and the V1/V2/V3 checks.
- **S3** gains V4 (vision QA). **S9** gains V5 (client cross-check) and the text-search fallback.

## Sources
- RAGFlow source (`internal/ingestion/task/indexdoc/position.go`, `internal/engine/vastbase/rows.go`) via GitHub code search
- [Docling — visual grounding example](https://docling-project.github.io/docling/examples/visual_grounding) · [DoclingDocument reference](https://docling-project.github.io/docling/reference/docling_document/)
- [LlamaIndex LiteParse — visual citations](https://developers.llamaindex.ai/liteparse/guides/visual-citations/)
- [OpenBB — citations for documents](https://docs.openbb.co/workspace/developers/ai-features/citations-for-documents)
- [Tensorlake — citation-aware RAG](https://tensorlake.ai/blog/rag-citations)
- [react-pdf-highlighter-extended](https://github.com/DanielArnould/react-pdf-highlighter-extended)
- [PDF.js multi-span highlight write-up](https://hashnode-hub.vercel.app/posts/685701a7e8732641687ac4f4) · [Nutrient — PDF.js text highlight](https://www.nutrient.io/blog/pdfjs-text-highlight-annotations/)
- [SciSpace — render citation highlights](https://docs.api.scispace.com/guides/citation-highlights.md)
- [Anthropic — search results](https://platform.claude.com/docs/en/build-with-claude/search-results) · [Anthropic — citations](https://platform.claude.com/docs/en/build-with-claude/citations)
- pdfplumber README (`dedupe_chars`, `force_mediabox`, `use_text_flow`, char `matrix`/`upright`)
