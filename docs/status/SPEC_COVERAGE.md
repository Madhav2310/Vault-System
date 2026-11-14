# Spec coverage (2026-11-13)

Source: `docs/spec/case-study.pdf`. "Done" = built and covered by passing tests. Percentages are my estimate for partial items.

## Backend components
| Requirement | Status | % | Note |
|---|---|---|---|
| Intelligent upload handler (type/size checks, dedupe, idempotency) | Done | 100 | |
| Smart document analysis (extraction, quality checks, OCR-needed detection) | Done | 100 | OCR itself not built; flagged `needs_ocr` |
| Async processing pipeline (Celery, leases, retries) | Done | 100 | Parallel embedding and brief calls |
| Upload & ingestion API | Done | 100 | New-version upload included |
| Document retrieval API (list, get, chunks, delete) | Done | 100 | |
| AI insights API (cited brief, tags, category, key facts) | Done | 100 | 56 of 58 facts verified in a live sample |
| Start chat session | Done | 100 | |
| Ask question (cited, RAG) | Done | 95 | Quality numbers not yet re-measured with working reranker |
| Get chat history | Done | 100 | Citations and follow-ups stored |
| Multi-turn context | Done | 100 | |
| Document statistics API | Done | 100 | |
| Processing metrics API | Done | 100 | Plus chat metrics, quota, feedback |
| Data storage: metadata, insights, chats, citations, metrics, performance | Done | 100 | 4 migrations |

## Stack
| Requirement | Status | Note |
|---|---|---|
| FastAPI, Postgres, Celery+Redis, pdfplumber/python-docx, OpenAI | Done | |
| Local/S3-compatible storage | Done | |
| RAG framework (LangChain/LlamaIndex) | Deliberately not used | Own pipeline; reason in README |
| pytest, API docs | Done | 800+ tests; OpenAPI + Postman |

## Bonus features
| Feature | Status | % |
|---|---|---|
| Multi-document chat | Done | 100 |
| Follow-up suggestions | Done | 100 (live quality eval pending) |
| Summary customization (length, tone, focus) | Done | 100 |
| Auto categorization and tagging | Done | 100 |
| Key insight extraction | Done | 100 |
| Sentiment analysis | Not built | 0 (not needed for the core story) |
| Comparative analysis between documents | In progress | 60 (lane running) |
| Frontend (library, source guide, chat, viewer) | Done | 90 (a11y pass running) |
| Real-time updates (processing status SSE) | Done | 100 |
| Smart caching (embeddings, retrieval results) | Done | 100 |
| Vector DB (pgvector) | Done | 100 |
| Streaming chat (SSE) | Done | 100 |

## Beyond the spec
Support check per citation, evidence gate, whole-document route, meta questions, stale-answer re-ask, daily cost quota, why-this-answer panel: done offline, quality numbers pending.

## Deliverables
| Deliverable | Status | % |
|---|---|---|
| Working backend | Done | 100 |
| Source repo | Done | 100 |
| README (setup, architecture, assumptions, scale) | In progress | 50 (lane running; scale doc done) |
| AI_USAGE.md | In progress | 70 (16 corrections logged; prompt log needs update) |
| Demo (document chat) | In progress | 30 (script lane running; recording is yours) |

## Overall
Required items: about 95%. Bonus items: about 85%. Documentation and demo: about 50%. Open measurements: retrieval and answer-quality baselines (live runs, about $3 each).
