/** Public API types. `schema.gen.ts` is generated (`make ui-types`); hand-written shapes live here. */
import type { components } from "./schema.gen";

export type Schemas = components["schemas"];

/** application/problem+json (design §12). */
export interface ErrorResponse {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string | null;
  retryable: boolean;
}

// The document endpoints return untyped dicts in OpenAPI, so their shapes are pinned here.
export type VersionStatus =
  | "uploaded"
  | "processing"
  | "ready"
  | "enriched"
  | "needs_ocr"
  | "unsupported"
  | "failed"
  | "superseded";

export interface VersionJson {
  id: string;
  version_no: number;
  filename: string;
  mime: string;
  size_bytes: number;
  page_count: number | null;
  status: VersionStatus;
  quality: unknown;
  error_code: string | null;
  created_at: string | null;
  ready_at: string | null;
}

export interface DocumentListItem {
  id: string;
  title: string;
  status: VersionStatus;
  category: string | null;
  tags: string[];
  summary_snippet: string | null;
  created_at: string | null;
  version: VersionJson;
}

export interface DocumentList {
  items: DocumentListItem[];
  next_cursor: string | null;
}

export interface UploadResponse {
  document_id: string;
  version_id: string;
  job_id: string | null;
  status: VersionStatus;
  deduplicated: boolean;
}

/** Processing SSE (`GET /v1/versions/{id}/processing/stream`, S14.05). */
export type ProcessingEvent =
  | { type: "stage"; name: string; status: string; attempt: number; at: string; error_code?: string }
  | { type: "done"; status: string; error_code: string | null }
  | { type: "timeout"; reason: string };

/** Chat turn SSE events (backend `app/chat/events.py`, `GET /v1/chat/events/schema`), discriminated on `type`. */
export type ChatStage =
  | "analyzing"
  | "retrieving"
  | "reading_documents"
  | "checking_evidence"
  | "writing"
  | "verifying"
  | "saving";

export interface EvidenceOut {
  chunk_id: string;
  document_id: string | null;
  document_title: string | null;
  version_no: number | null;
  pages: number[] | null;
  sentence_start: number;
  sentence_end: number;
  /** Null in history when the source document was deleted. */
  cited_text: string | null;
}

export type Verdict = "supported" | "partial" | "unsupported";

/** Two sources that disagree: the evidence of each side (`ConflictView` in `app/chat/conflicts.py`). */
export interface ConflictView {
  topic: string;
  side_a: EvidenceOut[];
  side_b: EvidenceOut[];
}

/** One option of a clarification: a real document of the chat's scope (S23.02). */
export interface ClarifyOption {
  label: string;
  document_id: string;
}

export type ChatEvent =
  | { type: "run.started"; run_id: string; message_id: string }
  | { type: "status"; run_id: string; stage: ChatStage }
  | { type: "segment"; run_id: string; index: number; text: string; support: "cited" | "none"; evidence: EvidenceOut[] }
  | { type: "verification"; run_id: string; verdicts: { index: number; verdict: Verdict | string }[] }
  | { type: "followups"; run_id: string; questions: string[] }
  | { type: "clarify"; run_id: string; question: string; options: ClarifyOption[] }
  | {
      type: "done";
      run_id: string;
      message_id: string;
      response_type: string;
      content: string;
      reason_codes: string[];
      usage: { input_tokens: number; output_tokens: number; cost_usd: number };
      latency_ms: number | null;
      ttft_ms: number | null;
      conflicts?: ConflictView[];
    }
  | { type: "error"; run_id: string; code: string; retryable: boolean };

export interface ChatSession {
  id: string;
  title: string | null;
  /** null = all documents */
  document_ids: string[] | null;
  created_at: string;
  updated_at: string;
  /** list only: the first question, cut to 120 characters */
  first_question?: string | null;
}

export interface ChatSessionList {
  items: ChatSession[];
  next_cursor: string | null;
}

/** A history segment (`GET /v1/chat/sessions/{id}/messages`); `ordinal` is the live event's `index`. */
export interface HistorySegment {
  ordinal: number;
  text: string;
  support: string;
  evidence: EvidenceOut[];
  /** Support-check verdict: `supported`, `partial`, `unsupported`; null when no check ran. */
  verification?: string | null;
  verification_reason?: string | null;
}

/** `{stale, since, newer_version_no}` (`history._staleness`); `newer_version_no` is null when no newer version is current. */
export interface Staleness {
  stale: boolean;
  since: string;
  newer_version_no: number | null;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  response_type: string | null;
  coverage_state: string | null;
  staleness: Staleness | null;
  run_id: string | null;
  segments: HistorySegment[];
  followups?: string[];
  conflicts?: ConflictView[];
  /** Options of a `clarify` message, in the order `clarify_choice.option` indexes. */
  clarify_options?: ClarifyOption[];
}

export interface HistoryList {
  items: HistoryMessage[];
  next_cursor: string | null;
}

/** `GET /v1/runs/{run_id}` ("Why this answer?"). */
export interface RunView {
  run_id: string;
  status: string;
  mode: string | null;
  route: string | null;
  response_type: string | null;
  reason_codes: string[];
  explanation: string[];
  sources: { document_title?: string | null; version_no?: number | null; pages?: unknown; chunks_used?: number }[];
  steps: { node: string; status: string; duration_ms?: number | null }[];
  usage: { calls?: number; input_tokens?: number; output_tokens?: number; cost_usd?: number };
}

export interface DocumentDetail {
  id: string;
  title: string;
  status: VersionStatus | null;
  current_version_id: string | null;
  versions: VersionJson[];
}

export type FeedbackRating = "up" | "down";
export type FeedbackReason = "wrong" | "unsupported" | "incomplete" | "other";

/** `GET /v1/metrics/{documents,processing,chat}` (`app/api/schemas.py`). Fields the page does not read are omitted. */
export interface Percentiles {
  p50: number | null;
  p95: number | null;
}

export interface DocumentsMetrics {
  scope: string;
  documents: number;
  by_status: Record<string, number>;
  by_mime: Record<string, number>;
  needs_ocr: number;
  pages: number;
  chunks: { total?: number } | null;
  storage_bytes: number;
  time_to_ready_seconds: Percentiles;
  enriched: number;
  by_category: Record<string, number>;
  facts: { total?: number; verified_ratio?: number | null };
}

export interface ProcessingMetrics {
  scope: string;
  window: string;
  throughput: { jobs_completed?: number; stage_runs?: number; jobs_per_hour?: number };
  stage_duration_ms: Record<string, { count?: number; p50?: number | null; p95?: number | null }>;
  failures_by_error_code: Record<string, number>;
  retries: unknown;
  oldest_pending_age_seconds: number | null;
}

export interface ChatMetrics {
  scope: string;
  window: string;
  /** UTC day, independent of `window`; `limit_usd` null = unlimited. */
  quota?: { limit_usd: number | null; spent_today_usd: number | null };
  turns: number;
  outcomes: Record<string, number>;
  coverage: Record<string, number>;
  latency_ms: Percentiles;
  ttft_ms: Percentiles;
  cost_per_turn_usd: Percentiles;
  degraded: Record<string, number>;
  feedback: { up: number; down: number; by_reason: Record<string, number> };
  llm: { calls: number; total_cost_usd: number | null; cache_hit_ratio: number | null };
}

export type MetricsWindow = "1h" | "24h" | "7d";
