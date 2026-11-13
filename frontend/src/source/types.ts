import { ApiError, getApiKey, NetworkError } from "../api/client";
import type { ErrorResponse } from "../api/types";

/** A citation as the API returns it (brief facts, custom summaries, chat segments). `cited_text` is stored text. */
export interface Citation {
  chunk_id: string;
  version_no: number;
  pages: number[] | null;
  section_path: string[] | null;
  sentence_start: number;
  sentence_end: number;
  cited_text: string;
}

/** `GET /v1/chunks/{id}` (ChunkOut is an untyped dict with extras in the OpenAPI, so it is pinned here). */
export interface ChunkView {
  id: string;
  document_id: string;
  ordinal: number;
  kind: string;
  text: string;
  sentences: unknown[];
  page_start: number;
  page_end: number;
  section_path: string[];
  token_count: number;
  version: { id: string; version_no: number; filename: string };
  /** Not in the API today; used when the backend adds neighbour ids. */
  prev_chunk_id?: string | null;
  next_chunk_id?: string | null;
}

async function toError(res: Response): Promise<ApiError> {
  let body: Partial<ErrorResponse> = {};
  try {
    body = (await res.json()) as Partial<ErrorResponse>;
  } catch {
    /* non-JSON error body */
  }
  const ra = Number(res.headers.get("retry-after"));
  return new ApiError(
    res.status,
    body.code ?? `http_${res.status}`,
    body.detail ?? body.title ?? res.statusText,
    body.retryable ?? false,
    Number.isFinite(ra) && ra > 0 ? ra : null,
  );
}

/** JSON request with the bearer key. Returns the status too, since summaries use 200 vs 202. */
export async function getJson<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<{ status: number; data: T }> {
  const key = getApiKey();
  const headers: Record<string, string> = key ? { Authorization: `Bearer ${key}` } : {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const req: RequestInit = { method: init.method ?? "GET", headers };
  if (init.body !== undefined) req.body = JSON.stringify(init.body);
  if (init.signal) req.signal = init.signal;
  let res: Response;
  try {
    res = await fetch(path, req);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError("Network error: could not reach the server.");
  }
  if (!res.ok) throw await toError(res);
  return { status: res.status, data: (await res.json()) as T };
}

export function friendlyError(e: unknown, what: string): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return `${what} was not found.`;
    if (e.status === 429) return `Too many requests.${e.retryAfterS ? ` Try again in ${e.retryAfterS} s.` : " Try again shortly."}`;
    return e.message || `Could not load ${what.toLowerCase()}.`;
  }
  if (e instanceof NetworkError) return e.message;
  return `Could not load ${what.toLowerCase()}.`;
}
