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

// TODO(S18.01): replace with the generated union from /v1/chat/events/schema once the chat SSE schema exists.
export type ChatEvent = { type: string; [k: string]: unknown };
