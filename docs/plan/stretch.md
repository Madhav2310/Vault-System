# Stretch (eval-gated; not required for completion)

Each item starts only after S10.11 and only if the user asks. Each is accepted only if its eval AC passes; otherwise it's reverted and the result recorded.

### X.01 — LLM contextual prefixes (`contextualize@1`)
Design: §6.3 (stage 7), §19
- [ ] AC1 Stage runs after `ready` in the background lane and re-embeds only affected chunks — `t:integration/ingestion/test_contextualize.py::test_upgrade_in_place`
- [ ] AC2 Prefix never appears in evidence blocks [A] — `t:unit/chat/test_evidence.py::test_no_prefix_in_evidence` (re-run)
- [ ] AC3 recall@8 improves ≥ 2 pp over baseline [R] — `$ make eval-retrieval CONFIG=contextual` vs baseline

### X.02 — Docling hi-fi extraction for flagged pages
Design: §6.3 (stage 8), §6.4
- [ ] AC1 Flagged pages re-extracted on queue `ingest-hifi` — `t:integration/ingestion/test_hifi.py::test_flagged_only`
- [ ] AC2 Table and multi-column golden categories improve ≥ 5 pp [R] — `$ make eval CONFIG=hifi` vs baseline

### X.03 — Vision OCR fallback for scanned pages
Design: §6.4
- [ ] AC1 Scanned fixture reaches `ready` with chunks, highlights at `precision=page` — `t:integration/ingestion/test_vision_ocr.py::test_scanned_ready`
- [ ] AC2 Cost per page recorded in `llm_calls` — same test

### X.04 — Per-sentence support badges
Design: §2 (Tier C), research 01 §3.2
- [ ] AC1 Async `verification` SSE event after `done` — `t:integration/chat/test_support.py::test_event`
- [ ] AC2 Checker agreement with 30 human labels ≥ 0.85 before UI exposure [T] — `$ python -m eval.support --agreement`

### X.05 — Export answers/threads with footnoted citations (MD/DOCX)
Design: §2 (Tier C), §12
- [ ] AC1 Export contains every segment and a footnote per citation with document, page and quote — `t:integration/chat/test_export.py::test_footnotes`

### X.06 — Version "what changed"
Design: §2 (Tier C), §23
- [ ] AC1 Section-level diff between v1 and v2 of a corpus agreement lists changed sections with both texts — `t:integration/insights/test_version_diff.py::test_changed_sections`

### X.07 — MCP server over retrieval
Design: §23
- [ ] AC1 Tools `search_documents`, `ask`, `get_brief` callable from an MCP client with API-key auth and owner scoping — `t:integration/mcp/test_tools.py::test_tools_scoped`

### X.08 — Provider failover adapter
Design: §4, §23
- [ ] AC1 Alternate adapter produces `EvidenceReference`s via application-managed citations — `t:integration/llm/test_failover.py::test_evidence_contract`
- [ ] AC2 Eval on the alternate provider within 5 pp of primary faithfulness before enabling [R]

### X.09 — ⌘K command bar and shareable read-only run links
Design: §2 (Tier C)
- [ ] AC1 ⌘K opens a palette that jumps to documents and starts a vault-wide question — `$ npx playwright test cmdk.spec.ts`
- [ ] AC2 Share link renders a read-only run view, requires an unguessable token, and is revocable — `t:integration/api/test_share.py::test_token_access_and_revoke`
