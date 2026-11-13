# UX patterns for document QA with source citations (state of the art, 2025-2026)

Scope note: this pass used 20 search/fetch calls. Several official help-center pages could not be read in full (index pages only, or binary PDFs). Every claim below has a link. Items marked [secondary] come from third-party write-ups, not vendor docs. Items marked [vendor claim] are marketing that was not independently verified.

## 1. How leading products present citations

### Takeaway
The pattern that has converged is: inline numbered markers, a hover preview of the exact quoted passage, and a click that opens the source at that passage with highlighting. Grid products (Hebbia, Harvey, Elicit) add citations per cell. The quote shown is source text, not text the model wrote. Products that only link to a file or page (Glean without deep links, ChatGPT file search through the API) are documented as weaker, and developers complain about them.

### Cited Findings
- **NotebookLM:** citations are direct quotes from the sources. In Google's words, a citation is "a specific block of text from your source that is considered relevant to the question and was used by NotebookLM to build the response". Users "can hover over a citation to see the complete text quoted." — [Google NotebookLM Help (search snippet)](https://support.google.com/notebooklm/answer/14276569?hl=en)
- **NotebookLM, Dec 2023 launch post (older source):** "NotebookLM automatically shares citations from your sources whenever it answers a question", and "now you can quickly jump from a citation to the source, letting you see the quote in its original context." — [Google blog, Dec 8 2023](https://blog.google/technology/ai/notebooklm-new-features-availability/)
- **NotebookLM, click behaviour:** hover previews the quoted text, and a click opens the source at that passage. [secondary] — [AI.Edge NotebookLM guide 2025 (PDF)](https://www.adventuresincre.com/wp-content/uploads/2025/07/NotebookLM-Quide-for-AI.Edge-Users-2025.pdf)
- **Observation, not verified:** in Nov 2026 the NotebookLM help-center URLs served pages titled "Gemini Notebook" (for example "Use chat in Gemini Notebook" and "Generate reports in Gemini Notebook"), which suggests a rebrand or merger. The pages were index-only and gave no detail on citation behaviour. — [support.google.com/notebooklm/answer/14276569](https://support.google.com/notebooklm/answer/14276569?hl=en), [support.google.com/notebooklm/answer/14290935](https://support.google.com/notebooklm/answer/14290935)
- **Anthropic Citations API (beta Jan 2025, GA mid-2025):** PDF and plain-text sources are "chunked into sentences", or the developer can supply custom chunks. Claude cites the passages it used. The quoted text does not count toward output tokens. — [Anthropic blog](https://claude.com/blog/introducing-citations-api); launch date per [Simon Willison, Jan 24 2025](https://simonwillison.net/e/8743); Bedrock GA [AWS, Jun 30 2025](https://aws.amazon.com/about-aws/whats-new/2025/06/citations-api-pdf-claude-models-amazon-bedrock)
- **Anthropic Citations, customer numbers [vendor claim]:** Endex said Citations cut "source hallucinations and formatting issues from 10% to 0%" and raised references per response by 20%. Anthropic says its internal evals show "recall accuracy" up "by up to 15%" over custom implementations. Thomson Reuters (CoCounsel) said building its own citation system had been hard to maintain. — [Anthropic blog](https://claude.com/blog/introducing-citations-api)
- **Anthropic `cited_text`:** it is a convenience field holding the exact cited span, and it is not billed as output. [secondary; check against the API docs] — [Maginative](https://www.maginative.com/article/anthropic-launches-citations-api-for-more-trustworthy-responses-from-claude/), [Simon Willison](https://simonwillison.net/e/8743)
- **Hebbia Matrix:** a grid where "rows are documents, columns are questions, and every cell is an AI-generated, cited answer". Users "can click on a cell in the table to see exactly where in the document an answer was sourced from" and "drill into any cell to see the AI's step-by-step reasoning chain and the precise source passage". [secondary] — [Contrary Research](https://research.contrary.com/reports/hebbia), [datastudios](https://www.datastudios.org/post/hebbia-ai-document-research-for-finance-matrix-workflows-and-pricing)
- **Hebbia, own blog:** outputs are "structured tables grouped by company and year, citation-linked so analysts can verify every data point." — [Hebbia blog](https://www.hebbia.com/blog/how-hedge-funds-use-hebbia)
- **Hebbia patent:** one patent covers "grid-cell highlighting based on LLM attention scores". It is not confirmed that the shipped product uses this. — [US 12626067 B2](https://www.goveda.com/patent/US-12626067-B2)
- **Harvey (Vault / Review Tables):** an Apr 2026 release note says cells include "transparent reasoning, sentence-level citations". Clicking a citation highlights the passage Harvey relied on. Since Jul 2025, exports can include a column of reference snippets. Manually entered columns have no citations or verification trail. In the API, inline markers use `[N]` pointing into the cell's citations array. — [Harvey help: Review Tables](https://help.harvey.ai/articles/using-review-tables), [Harvey release notes](https://help.harvey.ai/release-notes/file-as-column-context), [Export citations release note](https://eu.help.harvey.ai/release-notes/export-review-table-citations), [Harvey developer docs](https://developers.harvey.ai/guides/review_tables)
- **Elicit:** "To view the supporting quotes for any extraction, simply click on any cell or use your arrow keys to quickly navigate through the table." In reports, you can "click within the report to review supporting quotes and reasoning." If Elicit uses a table, "it will quote the table." — [Elicit help: systematic review steps 4-5](https://support.elicit.com/en/articles/4267649), [Elicit help: tables in column answers](https://support.elicit.com/en/articles/14758168-extracting-data-from-a-table-within-a-paper-in-column-answers)
- **Glean:** inline markers plus a "View sources" section, and hovering shows the cited text in context. "Deep-linked citations" (Q4 2025) open the exact passage. Without them, the link goes only to the parent document. Citations from tables and code blocks "display as simple plaintext". In fast mode, or when "No sources" is selected, there may be no citations. — [Glean docs: citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations), [Glean docs: deep-linked citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/deep-linked-citations)
- **ChatGPT, Projects and file search:** Visual Retrieval for PDFs is Enterprise-only, and "all other plans and document files only support text-based retrieval." Project file caps: 20 (Plus) or 40 (Pro/Team/Edu/Business). — [OpenAI help: optimizing file uploads](https://help.openai.com/en/articles/10029836-optimizing-file-uploads-in-chatgpt-enterprise)
- **OpenAI file_search API:** developers report "the extracted text has no page information so the models cannot know what is on each page". Citations are often file-level, so teams build their own viewers to jump to the passage. — [OpenAI community: PDF page identification errors](https://community.openai.com/t/pdf-page-identification-errors-with-file-search-on-assistants-v2-api-paging-problem-pages-not-in-chunk-metadata/793805), [OpenAI community: annotation text](https://community.openai.com/t/how-can-i-access-the-specific-text-of-the-file-that-the-annotation-is-referencing/726723)
- **Third-party PDF viewers:** CustomGPT opens the exact page with the cited text highlighted, for text-based PDFs. Tensorlake returns a page plus bounding box. [vendor claim] — [CustomGPT docs](https://docs.customgpt.ai/docs/how-pdf-citations-work), [Tensorlake](https://tensorlake.beehiiv.com/p/from-sketchy-data-to-trusted-ai-fresh-context-meets-field-level-citations)

### Inferences
- The table-stakes trio is a marker, a hover quote, and a click to the highlighted passage. Leading products show the stored source sentence, not a paraphrase. Vault already plans handles plus `cited_text` copied from stored sentences, which matches Anthropic's sentence-chunk design and NotebookLM's "citations are direct quotes" behaviour.
- Page-accurate jumps need page and offset metadata at ingest time (OpenAI forum evidence). Vault's glyph-mapped character stream is a real differentiator if it drives the highlight in the viewer.
- Citations per cell or per value (Hebbia, Harvey, Elicit) are the standard for multi-document comparison. Exporting citations with the values (Harvey) is a cheap, credible extra.

### Gaps
- No primary docs found for Perplexity's or Claude.ai Projects' citation UI (source cards, hover behaviour). They were not checked in this pass, so treat any description of them as unverified.
- No vendor documentation confirms that ChatGPT's in-app file citations highlight a PDF passage.
- No first-party Hebbia or Harvey screenshots or descriptions of hover behaviour were found. The evidence covers click behaviour only.

## 2. Onboarding and insight artifacts on upload; customizing summaries

### Takeaway
NotebookLM sets the reference pattern: an automatic per-source summary (the "Source guide"), starter and follow-up question suggestions, and one-click generated formats. Its help center now lists reports, mind maps, audio and video overviews, flashcards and quizzes, infographics and slide decks. Grid products make the "insight" the table itself.

### Cited Findings
- **NotebookLM Source guide:** clicking a source opens a "Source guide" card at the top of the Sources column, "where Gemini will generate a summary that describes the contents of the source." [secondary] — [Computerworld](https://www.computerworld.com/article/3712691/google-notebooklm-generative-ai-notes-app.html)
- **NotebookLM suggested questions:** it "automatically suggests followup questions for you, based on your recent conversation history and the content of your sources". Third-party guides describe starter questions in the "Notebook Guide". — [Google What's new (search snippet)](https://support.google.com/notebooklm/answer/14290935), [Computerworld](https://www.computerworld.com/article/1611774/google-notebooklm-generative-ai-notes-app.html)
- **NotebookLM, 2023 (older source):** "suggested actions" based on what the user is doing, and auto-suggested formats "like a thematic outline or study guide." — [Google blog, Dec 2023](https://blog.google/technology/ai/notebooklm-new-features-availability/)
- **NotebookLM / "Gemini Notebook", current help index:** articles on reports, Audio Overview, Video Overviews, Mind Maps, Flashcards/Quizzes, Infographic, Slide Decks, "Change mode" and "Change output language". These are titles only, with no detail. — [Google help index](https://support.google.com/notebooklm/answer/14290935)
- **Elicit:** recommends a pilot step where you review extracted values and supporting quotes "to gauge accuracy before running the full extraction". — [Elicit help](https://support.elicit.com/en/articles/4267649)

### Inferences
- A minimal Vault version: on ingest completion, show a per-document "source guide" (a summary of 3-5 key points, each with citations) plus 3-5 suggested questions generated from the document. Show suggested follow-ups after each answer. The design already has insights, so the UX move is to place them where the user lands right after upload.
- Customization in leading products takes the form of preset formats plus modes and language, not free-form prompt editing. A small set of summary presets (brief, detailed, key risks/obligations, timeline), plus length, is a defensible scope.

### Gaps
- No primary source was found in this pass for the 2024 Notebook Guide outputs (FAQ, Briefing doc, Timeline, Study guide). They are widely reported, but not verified here.
- No details on how NotebookLM's report customization works (custom prompt vs presets).

## 3. "Not found", partial answers, conflicting sources, multi-document comparison

### Takeaway
Explicit abstention is documented (NotebookLM says it "can't answer" and suggests rephrasing). Independent studies show most AI search tools rarely signal uncertainty. Structured grids (rows = documents, columns = questions, a cited answer per cell) are the leading answer to multi-document comparison. No product doc found handles "conflicting sources" as a first-class UI state.

### Cited Findings
- **NotebookLM abstention:** it "sometimes ... replies that it can't answer a question and suggests rephrasing it." [search snippet of official help] — [Google NotebookLM Help](https://support.google.com/notebooklm/answer/14276569?hl=en)
- **Glean on missing evidence:** citations show only "the sources they used, not the ones they missed", so citations do not prove retrieval was complete. [secondary blog] — [watts.it.com](https://watts.it.com/modules/grounded-answers-need-a-source-check)
- **Tow Center / CJR, Mar 2025:** 8 AI search tools, 200 tests. They gave incorrect answers to more than 60% of queries and "were generally bad at declining to answer questions they couldn't answer accurately". Perplexity had a 37% error rate and Grok 3 had 94%. The per-tool figures come from secondary coverage. — [Nieman Lab](https://www.niemanlab.org/2025/03/ai-search-engines-fail-to-produce-accurate-citations-in-over-60-of-tests/), [Fortune](https://fortune.com/2025/03/18/ai-search-engines-confidently-wrong-citing-sources-columbia-study/), [DCN](https://digitalcontentnext.org/blog/2025/03/24/ai-search-has-a-news-citation-problem)
- **Multi-document comparison:** the Hebbia Matrix grid (see §1). Harvey Review Tables have cited cells and grouping, and hovering a group previews which documents it contains. Elicit extraction tables have per-cell quotes. — [Contrary](https://research.contrary.com/reports/hebbia), [Harvey help](https://help.harvey.ai/articles/using-review-tables), [Elicit help](https://support.elicit.com/en/articles/4267649)
- **Tables as an extraction risk:** Elicit "occasionally fails to extract and use information from some tables, depending on table complexity and formatting." — [Elicit help](https://support.elicit.com/en/articles/14758168-extracting-data-from-a-table-within-a-paper-in-column-answers)

### Inferences
- A clearly designed "not found in your documents" state is a differentiator, because the measured norm is overconfidence. It should name what was searched (which documents) and offer next actions: rephrase, widen to all documents, or show the closest passages. For partial answers, mark each claim as supported or unsupported instead of using one confidence label for the whole answer. Vault's reason codes map onto this directly.
- Conflicts: show both cited passages side by side under a "Sources disagree" callout. This is inferred, as no product doc was found that does it. It could be a small, visible piece of product thinking.
- Comparison: a minimal matrix (documents × user-defined questions, a cited answer per cell, CSV export with snippets) mirrors Hebbia, Harvey and Elicit. The design already has `matrix`.

### Gaps
- No documentation found for how any of the eight products visually present conflicting sources.
- No primary Perplexity or ChatGPT docs on abstention behaviour.

## 4. Streaming and status; latency tolerance

### Takeaway
Agentic products show visible step plans (Glean shows numbered steps on a card). Evidence on time-to-first-token tolerance for LLM chat is thin. The classic HCI thresholds (0.1 s, 1 s, 10 s) are the usual anchor, and specific TTFT targets come from practitioner blogs, not studies.

### Cited Findings
- **Glean:** for complex tasks, Assistant "breaks the work into numbered steps shown on a card" and can add, remove or reorder steps while it runs. Fast mode may skip retrieval. — [Glean docs: plan](https://docs.glean.com/user-guide/assistant/glean-chat/plan), [Glean docs: citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations)
- **Classic thresholds (Nielsen-era, pre-2024 origin):** 0.1 s feels instantaneous, 1 s keeps flow of thought, 10 s risks losing attention. — [Redis blog](https://redis.io/blog/ttft-meaning/)
- **Controlled TTFT study:** arXiv 2604.06183 varied TTFT at 2, 9 and 20 s across "Creation" and "Advice" knowledge tasks. Its findings were not retrieved in this pass. — [arXiv 2604.06183](https://arxiv.org/html/2604.06183)
- **Practitioner claims [unverified]:** "sub-500ms p95 TTFT" as a target, and streaming "perceived 40-60% faster" with no source given. — [respan.ai](https://www.respan.ai/articles/time-to-first-token), [tianpan.co](https://tianpan.co/blog/2026/04/16/streaming-ttft-latency-perception)

### Inferences
- For Vault: stream tokens, and before the first token show a short real-progress status ("Searching 3 documents" → "Reading 8 passages" → "Writing"). Only show statuses that correspond to real pipeline stages, not fake spinners. Show document-processing status per file (queued → parsing → indexing → ready), with an error and retry state. A long-running ingest is where trust is won or lost (see the complaints below).
- Defensible stated targets: first status under 1 s, first token in a few seconds. Label these as product goals, not evidence-based thresholds.

### Gaps
- No peer-reviewed numbers were found on TTFT tolerance for document QA specifically. The arXiv 2604.06183 results were not read.
- Retrieval-step UIs in NotebookLM, ChatGPT, Claude and Perplexity were not verified from primary sources in this pass.

## 5. Documented user complaints

### Takeaway
Recurring complaints: citations that point to the wrong passage or don't match the PDF (worst on scans and table screenshots), table and number errors from flattened extraction, incomplete coverage across many sources, source-count limits, stale uploaded copies, and quality regressions after model updates. Few first-hand Reddit or HN threads were retrieved. Most evidence is secondary.

### Cited Findings
- **NotebookLM:** the most misleading error is an answer whose citation doesn't match the PDF, most often with scanned PDFs, table screenshots and image slides. [secondary, Portuguese] — [horadecodar](https://horadecodar.com.br/notebooklm-erra/)
- **NotebookLM:** footnotes and appendix tables that change core figures can be lost in chunking, which causes numeric inconsistencies. [secondary] — [remio.ai](https://www.remio.ai/post/notebooklm-deep-research-the-huge-gap-between-promise-and-reality)
- **NotebookLM:** a practitioner review cites screenshots of fabricated citations and says the most consistent Reddit/HN complaint is the source limit. [secondary] — [Enterprise DNA](https://enterprisedna.co/resources/blog/practitioner-notebooklm-review); [vantaige](https://vantaige.io/ai-tool/notebooklm)
- **NotebookLM regression:** a Google AI forum thread reports that grounding and retrieval across large notebooks broke after the Feb 19 Gemini 3.1 Pro update. — [discuss.ai.google.dev](https://discuss.ai.google.dev/t/critical-regression-gemini-3-1-pro-update-feb-19-completely-broke-notebooklm-s-rag-grounding/126857)
- **r/notebooklm:** "NotebookLM can't consider all sources" (Apr 2025). A user cross-referencing two Markdown tables found only some entries handled. — [r/notebooklm via redlib](https://redlib.hbubli.cc/r/notebooklm/comments/1jzn6ai/notebooklm_cant_consider_all_sources), [r/notebooklm thread](https://redlib.hbubli.cc/r/notebooklm/comments/1l0b1fb/notebooklm_doesnt_seem_to_be_having_access_to_the)
- **Stale copies:** uploaded files are imported as copies, so edits to the original don't propagate. — [toolnavs](https://toolnavs.com/en/article/1917-notebooklm-answer-doesnt-cite-or-doesnt-match-first-check-the-source-scope)
- **OpenAI file search:** wrong page numbers, because chunks lack page metadata. — [OpenAI community](https://community.openai.com/t/pdf-page-identification-errors-with-file-search-on-assistants-v2-api-paging-problem-pages-not-in-chunk-metadata/793805)
- **Tables generally:** naive PDF extraction concatenates cells, so column relationships vanish. Tables that span pages lose their headers. [vendor/blog] — [Nutrient](https://www.nutrient.io/blog/why-your-ai-agent-hallucinates-pdf-table-data.md), [HackerNoon](https://hackernoon.com/your-rag-pipeline-dies-on-real-documents-heres-why), [TDS: Don't flatten the grid](https://towardsdatascience.com/tables-in-pdfs-for-rag-dont-flatten-the-grid/)
- **"Right file, wrong passage"** happens when document discovery succeeds but chunk retrieval or citation mapping fails. [blog] — [ZimaSpace](https://shop.zimaspace.com/en-ca/blogs/tech-ai-hub/rag-pipeline-cites-right-file-wrong-passage)
- **Glean** admits that table citations render as plaintext and are hard to read. — [Glean docs](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/deep-linked-citations)

### Inferences
- The top-ranked complaint, "the citation doesn't match", is exactly what Vault's architecture (handles, stored sentences, glyph-mapped offsets) prevents by construction. Make that visible: the hover shows the exact stored sentence, and the click highlights the same span in the rendered page. Say so in the README.
- Tables: render table-derived citations as a small table, not flattened text. Where extraction confidence is low (scanned or image pages), warn the user at upload time instead of failing silently at answer time.

### Gaps
- No HN threads were retrieved, and the search returned no HN results. Reddit evidence is thin (two threads). Treat "most common complaint" rankings as secondary-source opinion.
- No complaint evidence was gathered on "lost context in long chats".

## 6. HCI and research evidence on citation trust, verification and over-reliance

### Takeaway
Citations increase perceived trust, but many are unsupported. Liu et al. found only 74.5% of citations supported their sentence. Sources reduce reliance on wrong answers, while explanations increase reliance on both right and wrong answers. Users over-rely on LLM search when it is wrong. These findings favour UI that makes verification cheap (exact quotes, one-click highlight) and that marks unsupported claims, over persuasive prose.

### Cited Findings
- **Liu, Zhang, Liang (Findings of EMNLP 2023, older source):** across Bing Chat, NeevaAI, perplexity.ai and YouChat, "a mere 51.5% of generated sentences are fully supported with citations (citation recall), and only 74.5% of citations support their associated sentence (citation precision)". Recall and precision are inversely correlated with fluency and perceived utility. — [arXiv 2304.09848](https://arxiv.org/abs/2304.09848), [Stanford HAI](https://hai.stanford.edu/news/generative-search-engines-beware-facade-trustworthiness)
- **Kim, Wortman Vaughan, Liao, Lombrozo, Russakovsky (CHI 2025):** a think-aloud study plus a pre-registered experiment (N=308). Explanations increase reliance on both correct and incorrect responses. There is less reliance on incorrect responses when sources are provided or explanations are inconsistent. — [Microsoft Research](https://www.microsoft.com/en-us/research/publication/fostering-appropriate-reliance-on-large-language-models-the-role-of-explanations-sources-and-inconsistencies), [Princeton](https://cognition.princeton.edu/publications/fostering-appropriate-reliance-large-language-models-role-explanations-sources-and)
- **Spatharioti, Rothschild, Goldstein, Hofman (CHI 2025):** LLM search vs traditional search. Accuracy was comparable when the LLM was correct, but users over-relied when it erred. The authors suggest communicating uncertainty as mitigation. The full PDF could not be parsed, so intervention details are unverified. — [Microsoft Research](https://www.microsoft.com/en-us/research/?p=1134789), [PDF](https://dangoldstein.com/papers/spatharioti_rothschild_goldstein_hofman_LLM_Search_CHI25.pdf)
- **"Behavioral Indicators of Overreliance" (arXiv 2602.11567, 2026; venue unconfirmed):** 77 participants used an LLM with injected misinformation. Over-reliant users kept referring back to the LLM. Even those who hesitated and asked follow-ups ultimately adopted its answer. — [arXiv](https://arxiv.org/html/2602.11567v1)
- **PaperTrail (arXiv 2602.21045):** argues that citations improve perceived trust but are sometimes hallucinated or inaccurate, and proposes argument-grounded provenance. — [arXiv](https://arxiv.org/pdf/2602.21045v1)
- **Citations "make verification faster, but they do not remove the need for it."** [secondary] — [allmind compare](https://allmind.ai/compare/allmind-vs-hebbia)

### Inferences
- Design implications for Vault: (a) keep answers terse and cite every claim, because fluent explanation inflates reliance; (b) show the exact supporting sentence inline on hover, since sources reduce over-reliance on wrong answers; (c) visibly flag sentences that have no resolved citation (Vault's `CITATION_UNRESOLVED` → render as "unsupported" rather than silently dropping); (d) report citation precision and recall as a metric in the metrics API. This is the Liu et al. framing, and graders will recognise it.

### Gaps
- No study was found that directly compares hover-preview vs click-to-source vs side-panel citation layouts on verification time or accuracy.
- The Spatharioti et al. intervention details (for example, highlighting) were not verified.

## 7. Which patterns a small team can realistically build (minimal web UI + API)

### Takeaway
Most of the trust-critical patterns are cheap once ingestion keeps exact offsets. The expensive ones are pixel-accurate PDF highlighting, audio and video overviews, and agentic multi-step plans. A small team should ship the cheap trust set first and frame the rest as roadmap.

### Cited Findings
- Anthropic's own design (sentence chunking, quotes returned verbatim and unbilled) shows the sentence-level-handle approach is production-standard, not exotic. — [Anthropic blog](https://claude.com/blog/introducing-citations-api)
- Building your own citation system is described as hard to maintain (Thomson Reuters), and OpenAI-API developers have to build their own passage viewers. — [Anthropic blog](https://claude.com/blog/introducing-citations-api), [OpenAI community](https://community.openai.com/t/how-can-i-access-the-specific-text-of-the-file-that-the-annotation-is-referencing/726723)
- Grid comparison with cited cells and snippet export is core to Hebbia, Harvey and Elicit. — [Harvey export release note](https://eu.help.harvey.ai/release-notes/export-review-table-citations), [Elicit help](https://support.elicit.com/en/articles/4267649)

### Inferences (effort labels are judgement, not measured)
- **Low effort, high trust value (build):**
  1. Inline `[n]` markers with a hover card showing the exact stored sentence, document name and page.
  2. A click opens a side panel with the document text scrolled to the sentence and highlighted. Use a text view first; pdf.js page plus text-layer highlight is a stretch goal.
  3. A sources list under the answer.
  4. An explicit "Not found in your documents" state that says what was searched and offers next actions.
  5. Claims without citations flagged as unsupported.
  6. Per-document processing status with retry.
  7. A source-guide card after upload: summary plus key points with citations, plus suggested questions.
  8. Suggested follow-ups after answers.
  9. A scope selector: this document, selected documents, or all documents.
- **Medium effort:**
  1. A comparison matrix (documents × questions, cited cells, CSV export with snippets).
  2. A "Sources disagree" callout showing both passages.
  3. Real pipeline status before the first token.
  4. A citation-precision metric in the metrics API.
- **High effort or skip for a take-home:** pixel-accurate bounding-box highlights on scanned PDFs, audio/video overviews, mind maps, agentic step plans, and visual retrieval of figures.
- The best narrative for graders is that the top documented complaint, a citation that doesn't match its source, is prevented by construction, and the UI makes that checkable in one hover.

### Gaps
- No measured build-cost data. The effort tiers above are engineering judgement.
