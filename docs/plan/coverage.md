# Coverage / traceability

Every requirement maps to at least one task. A row with no task must say why (out of scope / deferred).
Review log at the bottom.

---

## A. Assignment spec (`docs/spec/case-study.pdf`) → tasks

| Spec item | Tasks |
|---|---|
| Intelligent upload handler | S1.01, S1.02, S1.03, S1.04, S1.05 |
| Smart document analysis | S1.13–S1.19 (structure, quality), S6.02–S6.05 (brief, tags, suggestions) |
| Asynchronous processing pipeline | S0.14, S1.07–S1.11, S1.24, S1.25, S6.04 |
| Upload & ingestion API | S1.01, S1.06, S1.27, S5.05 |
| Document retrieval API | S1.27, S1.29 |
| AI insights API | S6.06, S6.07, S6.09 |
| Start chat session | S3.01 |
| Ask question | S3.03, S3.05–S3.10, S4.*, S5.01 |
| Get chat history | S3.11 |
| Multi-turn context | S3.04, S3.05 (standalone query), S4.15 (rolling summary, entities) |
| Document statistics API | S7.01 |
| Processing metrics API | S7.02 (+ S7.03 chat metrics) |
| Data: document metadata and status | S0.08, S1.04, S1.09 |
| Data: AI-generated summaries and insights | S0.08, S6.04, S6.07 |
| Data: chat conversations and history | S0.08, S3.01, S3.10 |
| Data: chat messages with context and citations | S3.10 (segments, evidence, runs) |
| Data: processing metrics and history | S1.09 (`job_events`), S7.02 |
| Data: system performance data | S0.13 (`llm_calls`), S3.10 (`rag_runs`), S7.04, S7.05 |
| Python / FastAPI | S0.02 |
| PostgreSQL | S0.04, S0.08 |
| Task queue (Celery + Redis) | S0.14 |
| File processing libraries | S1.12–S1.18 |
| LLM API (OpenAI) + embeddings | S0.13, S0.23, S0.24, S1.24 |
| Storage (local / S3) | S0.11, S0.12 [OPT] |
| RAG framework (recommended, optional) | Not used by design (design §21 D1); rationale documented in README (S10.03) |
| API documentation (FastAPI docs) | S7.14 AC1, S10.03 |
| Testing (pytest) | Every task; S10.09 |
| Deliverable: working backend | S10.11 |
| Deliverable: source repository | S0.01 |
| Deliverable: README | S10.03–S10.06, S10.08 |
| Deliverable: AI_USAGE.md | S0.03, S10.07 (+ per-task updates) |
| Deliverable: demo (document chat) | S10.10 |
| "Think about scale" | S2.20, S5.12–S5.15, S10.06, S10.08 |
| "Document assumptions" | S10.03 (assumptions & trade-offs section) |
| "Prompt engineering thought process" | S3.07, S4.12, S10.07 (prompt iteration log with eval deltas) |
| Bonus: multi-document chat | S4.07 |
| Bonus: follow-up suggestions | S6.08 |
| Bonus: summary customization | S6.07 |
| Bonus: categorization and tagging | S6.05 |
| Bonus: sentiment / key insight extraction | S6.02 (key points, unusual clauses). Sentiment not built: low value for document types in scope (design §1.4 spirit); noted in README limitations (S10.03). |
| Bonus: comparative analysis | S4.07, S4.09, S8.* |
| Bonus: frontend | S9.* |
| Bonus: real-time updates | S5.01 (chat SSE), S5.05 (processing SSE), S8.04 (matrix SSE) |
| Bonus: smart caching | S2.02, S2.10, S3.08 AC6 (prompt cache), S4.13 AC5, S6.01 |
| Bonus: vector database | S0.08, S2.03, S2.04 (pgvector) |
| Bonus: streaming responses | S5.01–S5.04 |
| Bonus: comprehensive tests | all tasks; S10.09 |
| Bonus: cost tracking | S0.13, S3.10 AC3, S7.03 |
| Bonus: rate limiting and quotas | S5.06, S5.08, S5.09 |
| Bonus: document version tracking | S1.06, S1.25, S6.10 |
| Bonus: batch processing optimizations | S1.24 (embedding batching), S8.03 (fan-out) |
| Bonus: logging and observability | S0.06, S0.15, S1.31, S2.11, S3.14, S7.* |
| Bonus: health checks | S0.16 |

## B. Design (`docs/design.md`) → tasks

| Design section / item | Tasks |
|---|---|
| §1 Product, principles, non-goals | Enforced by CLAUDE.md rules; S4.02 (explanations from codes), S3.15/S4.17 (measure before adding) |
| §2 Tier A: exact-sentence highlight | S1.20, S1.29, S3.06, S3.09, S3.13, S9.08 |
| §2 Tier A: evidence-first answers + footer | S3.07, S3.12, S9.10 |
| §2 Tier A: passages panel | S3.12, S9.10 |
| §2 Tier A: coverage & conflicts | S4.09, S4.12, S9.11 |
| §2 Tier A: honest abstention | S4.08, S4.11, S9.11 |
| §2 Tier A: processing timeline + quality badges | S1.19, S1.27, S5.05, S9.04 |
| §2 Tier A: brief card + suggested questions | S6.02–S6.06, S9.05 |
| §2 Tier B: compare matrix | S8.*, S9.15 |
| §2 Tier B: living answers | S6.10, S9.14 |
| §2 Tier B: vault views (dates, filters) | S6.05 AC3, S6.09, S9.14 |
| §2 Tier B: feedback | S6.11, S6.12, S9.14 |
| §2 Tier C: export, support badges, OCR fallback, version diff, ⌘K, share link | stretch X.05, X.04, X.03, X.06, X.09 |
| §2 Answer format policy (no tables, answer-first, per-route) | S3.07, S4.05, S4.07 AC2, S4.11, S9.11 AC1 |
| §3 Topology & boundaries | S0.04, S2.07, S0.16 |
| §4 Model defaults & IDs | S0.05 AC4, S0.13, S4.20 AC5 |
| §4 Citations ≠ structured outputs | S0.13 AC11, S3.08 AC1 |
| §4 Effort per route | S0.13 AC12, S3.05 AC4, S3.07 AC2 |
| §4 stop_reason handling | S0.13 AC6–AC7, S3.08 AC3–AC4 |
| §4 Native OTel metadata-only | S0.15 AC5 |
| §4 No silent failover / 503 | S5.07 AC4; failover → X.08 |
| §5.1 Module map | S0.02 (layout), each module's task |
| §5.2 Core types | S1.12, S1.22, S3.09, S4.01 |
| §5.3 Gateway contract | S0.13, S5.06, S5.07 |
| §6.1 Upload steps 1–7 | S1.01–S1.05, S1.07 |
| §6.2 Status machine (incl. superseded) | S1.09 AC6, S1.17, S1.19, S1.25, S6.04 |
| §6.3 Stages 1–6 | S1.13–S1.19 (1–2), S1.22/S1.20 (3), S1.24 (4), S1.25 (5), S6.04 (6) |
| §6.3 Stages 7–8 | stretch X.01, X.02 |
| §6.4 PDF extraction + word boxes | S1.13, S1.16, S1.20 |
| §6.4 Scanned / encrypted / corrupt / DOCX / TXT / bombs | S1.19, S1.17, S1.18, S1.12 |
| §6.5 Chunker rules 1–8 | S1.21, S1.22, S1.23 |
| §6.6 Claim, heartbeat, stage commit, failure, sweeper, Celery config, fairness | S1.08, S1.09, S1.10, S0.14, S1.11 |
| §6.7 Deletion | S1.26 |
| §7.1 Scope | S2.01 |
| §7.2 Partitions, halfvec, tsvectors, exact search, HNSW escape hatch | S0.08, S1.23, S2.03, S2.04, S2.20 |
| §7.3 Pipeline (embed, vector, lexical, RRF, rerank, expansion, record, speculative) | S2.02–S2.10, S4.06 |
| §7.4 Caches (incl. "answers never cached") | S2.02, S2.10, S1.24, S6.01, S3.08 AC6; never-cache enforced by CLAUDE.md rule + absence of answer cache (code review in S3.17) |
| §8.1 Harness + route limits table | S4.01, S4.14 |
| §8.2 Graph | S3 (linear path), S4.03–S4.13 |
| §8.3 Nodes (each) | guard: S3.02/S3.03/S2.01 · load_memory: S3.04 · analyze_query: S3.05 · meta_ops: S4.04, S6.09 · select_mode: S4.13 · retrieve/rerank/expand: S2.*, S4.07 · evidence_gate: S4.08, S4.09 · generate: S3.08 · resolve_citations: S3.09 · persist: S3.10 · release_turn: S3.02 |
| §8.4 Tiered gate + policy | S4.08, S4.09, S4.10, S4.17 |
| §4.1–4.2 model roles, routing table, effort support, role fallback | S0.24, S4.19 |
| §4.4 OpenAI API facts (Responses, streaming, caching, limits) | S0.23, S0.25, S5.06 |
| §8.5 handles, strict answer schema, manifest, code-side resolution, highlight, caching, injection stance | S3.06, S3.09, S1.29, S3.08, S4.13, S3.07 (injection stance in prompt) |
| §8.6 SSE events, heartbeat, disconnect, reconnect, JSON mode | S5.01–S5.04 |
| §8.7 Session CAS, client_message_id, no connection held | S3.02, S3.03 |
| §8.8 Memory types | S3.04, S4.15 |
| §9.1 Brief + verification + map-reduce + suggestions + tags | S6.02–S6.05 |
| §9.2 Custom summaries | S6.07 |
| §9.3 Follow-ups | S6.08 |
| §9.4 Matrix | S8.01–S8.06 |
| §9.5 Staleness | S1.25 AC4 (hook), S6.10 |
| §9.6 Feedback loop | S6.11, S6.12 |
| §10.1 Reason codes | S4.02 |
| §10.2 Views (user, operator, developer) | S3.12, S4.16, S9.10, S9.13 |
| §10.3 Replay | S4.16, S9.13 |
| §11 Every table | S0.08 AC3 (existence), plus the owning feature task for each table |
| §11 Retention (idempotency 24 h, job_events 90 d) | S1.05 AC5, S1.09 AC7 |
| §12 Conventions (problem+json, cursor, auth, Idempotency-Key, RateLimit headers) | S0.07, S1.27 AC1, S0.09, S9.03, S1.05, S5.08 AC4 |
| §12 Documents endpoints (each) | S1.01, S1.06, S1.27, S1.26, S5.05, S6.06, S6.07, S1.29 |
| §12 Chat endpoints (each) | S3.01, S3.11, S3.03/S5.01, S3.12/S4.16, S4.16 (replay), X.05 (export) |
| §12 Intelligence & feedback endpoints | S8.01, S8.04, S8.06, S6.09, S6.11 |
| §12 Metrics & health | S7.01–S7.04, S0.16 |
| §13.1 Concurrency model | S3.02 AC4, S5.10, S2.07 |
| §13.2 Rate limiter + breaker | S5.06, S5.07 |
| §13.3 Capacity estimates | validated by S5.12, S5.14; documented S10.06, S10.08 |
| §13.4 Backpressure & fairness | S5.08, S5.09, S1.11, S8.03 AC2 |
| §14 Idempotency (each row) | POSTs: S1.05 · upload: S1.04 · dispatch: S1.07 · task: S1.08 · stage outputs: S1.09 AC2 · llm cache: S6.01 · chat message: S3.03 · session: S3.02 · followups/compaction: S6.08, S4.15 · staleness: S6.10 · summaries/briefs: S6.07, S6.04 · matrix: S8.03, S8.05 · feedback: S6.11 · deletion: S1.26 · beat: S0.04 AC5 |
| §15 Failure modes (each area) | Upload: S1.01/S1.02/S1.17/S1.18 · extraction: S1.19 · jobs: S1.08–S1.10, S1.32 chaos · providers: S0.13, S5.07, S2.02 · retrieval: S2.03, S2.05, S2.09, S2.08 · grounding: S4.09–S4.12, S3.09 · runtime: S3.02, S4.14, S5.04, S4.13 · platform: S0.16, S5.13, S5.08 · drift: S7.09, S7.10 |
| §16 Security (each) | Identity: S0.09, S9.03 · authZ & 404: cross-user ACs in every resource task, S0.10 · upload safety: S1.01, S1.02, S1.17, S1.18, S0.11 AC4 · telemetry minimization: S0.15, S0.17, S1.31 AC4, S3.17 AC1, S7.06 AC2 · secrets: S0.05 AC3, S10.11 AC4 · licensing: S2.07 AC4, S0.18 (licenses recorded per document in `documents.yaml`) |
| §17 Observability (instrumentation, backend, collector, logs, labels, dashboards, SLOs) | S0.06, S0.15, S1.31, S2.11, S3.14, S7.04–S7.11, S7.12/S7.13 [OPT] |
| §18 Evaluation (corpus, golden, metrics, ablations, calibration, judge hygiene, commands) | S0.18, S2.12–S2.20, S3.15, S4.17, S7.10, S10.05 |
| §19 Prompts (each) | analyze_query: S3.05 · evidence_gate: S4.09 · answer_rag: S3.07, S4.12 · answer_full: S4.13 · brief_extract: S6.02 · summary: S6.07 · followups: S6.08 · memory_compact: S4.15 · contextualize: X.01 · eval_judge_*: S2.18 |
| §20.1 Dev compose (+ optional profiles) | S0.04, S0.12, S7.13, S9.02 |
| §20.2 1000-DAU topology | S5.10 (PgBouncer), S10.08 |
| §21 Decision log | Realized by the tasks above; summarized in README (S10.03) |
| §22 Unknowns (each) | tier limits: S0.18 AC7 · cache/TPM: resolved from OpenAI docs (cached tokens count toward TPM; S5.14 AC5 re-confirms from headers) · reranker: S2.07, S2.19 · exact-search latency: S2.20 · halfvec: S2.20 AC4 · gate bands/threshold: S4.17 · extraction quality: S1.13, S1.16, S2.17 · Docling: X.02 · contextual prefixes: X.01 · TTFT split: S5.12 · judge reliability: S2.18 |
| §23 Deferred | Documented: S10.08 AC1; optional builds: stretch.md |

## C. Research-doc commitments not phrased as design sections

| Commitment | Tasks |
|---|---|
| research 03 §4 metric catalog | S7.04 (catalog test) + emitting tasks S1.31, S2.11, S3.14, S4.08–S4.12, S5.06, S5.07, S5.10 |
| research 03 §5 SLOs and alerts | S7.08, S7.09, S7.10 |
| research 03 §6 dashboards D1–D6 | S7.07 |
| research 03 §7 eval as observability | S7.10 |
| research 04 §5 per-user limits | S5.08 |
| research 04 §7 load/chaos plan and pass criteria | S5.11–S5.15 |
| research 01 §4 builder-mistake guardrails | S1.22 (chunking), S2.05/S2.06 (hybrid), S2.08 (rerank), S4.13 (full context), S0.18/S2.13 (third-party expert-labelled eval), S1.19 (scans), S1.25 (versions), S2.01 (permissions), S7.* (observability) |

---

## D. Research passes 5–6 (geometry chain, convergence engine) → tasks

| Commitment | Tasks |
|---|---|
| Glyph-mapped character stream (I1), ligature/multi-char expansion, dedupe, tuned extraction config | S1.13 |
| CropBox/rotation normalization (F5, F6, F12) | S0.21 (spike), S1.14 |
| Reading-order repair and footnote filter (F2–F4) | S1.15 |
| Line-end hyphenation only in `embedding_text` (F9) | S1.22 (chunker AC) |
| Rectangle builder and precision ladder | S1.28, S1.29 |
| Validation V1–V3 (round-trip, containment, fixture zoo) | S0.18 (zoo), S1.30 |
| V4 vision QA | S3.16 |
| V5 client cross-check and text-search fallback | S9.09 |
| De-risking spikes (citations, schema, geometry, collector) | S0.19, S0.20, S0.21, S0.22 |
| Vault-Eval v1 dataset (122 items, 15 docs, expert labels, holdout) | S0.18, S2.13 |
| Eval engine (stage cache, Batch, spend ledger, state) | S2.14 |
| Noise floor and paired-bootstrap decisions | S2.15 |
| Failure attribution | S2.16 |
| Stage A extraction tuning | done during planning (`eval/build/extraction_sweep.py`); regression-checked by S1.13 AC8 |
| Stage B retrieval optimizer | S2.19 |
| Stage C calibration | S4.17 |
| Stage D prompt hill-climb | S4.18 |
| Stage E ablations and convergence run | S4.20 |
| Inter-judge agreement now; human agreement at the end | S2.18, S10.01 |
| Final golden-set review and post-review re-evaluation (user decision) | S10.01, S10.02 |
| Brief extraction with handle citations | S6.02, S6.03 |
| Scale simulated (DB model, capacity simulation) | S2.20, S5.12 |
| Architecture diagrams | S10.04 |

## Review log

| Review | Date | Method | Findings | Fixes |
|---|---|---|---|---|
| 1 | 2026-11-12 | Design §-by-§ and spec line-by-line walk against the task list (this matrix) | Design gaps: no admin flag for operator views; no request field for "Search deeper"; no processing-status stream (spec bonus: real-time updates); no matrix size cap; no login rate limit; summaries for over-threshold documents undefined; feedback `eval_status` values undefined. Plan gaps: no admin user seeded; "answers never cached" had no AC; prompt-injection stance had no AC; highlight-precision eval metric missing; full-context TTFT SLO never measured; no injection fixture | Design §11, §12, §16, §9.2, §9.4 patched; S0.09 AC6, S3.17 AC3–AC4, S3.15 AC5, S5.14 AC3, S0.18 injection fixture added; S8.01 dependency corrected to S9.13 |
| 2 | 2026-11-12 | `scripts/plan_lint.py` (checklist ↔ headings, deps exist, AC numbering, every AC has one verification, no test reused across ACs, coverage refs exist, every non-gate task covered) + grep-assisted re-read for compound ACs | 3 ACs without a verification; 1 'report' AC without a pass condition; 9 compound ACs bundling two behaviors; 8 coverage references shifted by renumbering | Fixed all; lint result: 174 tasks, 687 ACs, 527 distinct tests, 0 errors, 0 warnings; every coverage AC reference re-verified against its text |

## Plan v2 (2026-11-13)
S11.* retrieval quality, latency, abstention · S12.* document intelligence (spec: Smart Document Analysis, AI Insights, summary customization, categorization/tagging) · S13.* chat experience (spec: multi-turn, streaming, follow-ups, multi-document and comparative chat) · S14.* production readiness (spec: rate limiting, quotas, version tracking, real-time status) · S15.* delivery (spec: README, AI_USAGE, demo, frontend).

## Plan v3 (2026-11-13)
S16.* lean environment, reranker RCA, extraction memory, README skeleton · S17.* retrieval and answer quality (paired A/B, whole-document routing, gate, support check, answer baseline) · S18.* chat experience API (SSE, explanations, follow-ups, meta, compare, conflicts, quota, staleness, OpenAPI) · S19.* web UI · S20.* delivery (README, diagrams, evaluation, scale, AI_USAGE, demo, fresh clone).
