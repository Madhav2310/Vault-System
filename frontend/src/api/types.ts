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

export type ChatEvent =
  | { type: "run.started"; run_id: string; message_id: string }
  | { type: "status"; run_id: string; stage: ChatStage }
  | { type: "segment"; run_id: string; index: number; text: string; support: "cited" | "none"; evidence: EvidenceOut[] }
  | { type: "verification"; run_id: string; verdicts: { index: number; verdict: Verdict | string }[] }
  | { type: "followups"; run_id: string; questions: string[] }
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
    }
  | { type: "error"; run_id: string; code: string; retryable: boolean };

export interface ChatSession {
  id: string;
  title: string | null;
  /** null = all documents */
  document_ids: string[] | null;
  created_at: string;
  updated_at: string;
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
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  response_type: string | null;
  coverage_state: string | null;
  /** Shape not pinned by the backend yet (null today); the UI reads `stale` and `current_version_no` defensively. */
  staleness: Record<string, unknown> | null;
  run_id: string | null;
  segments: HistorySegment[];
  followups?: string[];
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
