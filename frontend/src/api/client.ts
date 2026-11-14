import { SseHttpError, streamEvents, type SseFrame } from "../sse";
import type {
  DocumentList,
  ChatMetrics,
  DocumentsMetrics,
  MetricsWindow,
  ProcessingMetrics,
  ErrorResponse,
  ProcessingEvent,
  UploadResponse,
  VersionStatus,
} from "./types";

/**
 * Auth is an httpOnly session cookie set by POST /v1/session (S22.11): the key is never stored in the page.
 * State-changing requests carry this custom header, which the server requires for cookie auth (CSRF guard).
 */
export const CSRF_HEADERS: Record<string, string> = { "X-Requested-With": "vault-ui" };

/** A mapped API failure. `retryAfterS` comes from Retry-After on 429. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryable: boolean,
    public readonly retryAfterS: number | null = null,
  ) {
    super(message);
  }
}

export class NetworkError extends Error {}

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

function authHeaders(): Record<string, string> {
  return CSRF_HEADERS;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, credentials: "same-origin", headers: { ...authHeaders(), ...init.headers } });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError("Network error: could not reach the server.");
  }
  if (!res.ok) throw await toError(res);
  return res;
}

export interface ListParams {
  q?: string;
  category?: string;
  tag?: string;
  status?: VersionStatus;
  cursor?: string;
  limit?: number;
}

export const api = {
  async listDocuments(p: ListParams = {}, signal?: AbortSignal): Promise<DocumentList> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== "") qs.set(k, String(v));
    const init: RequestInit = signal ? { signal } : {};
    return (await request(`/v1/documents?${qs}`, init)).json() as Promise<DocumentList>;
  },

  async upload(file: File): Promise<UploadResponse> {
    const form = new FormData();
    form.append("file", file);
    const key = crypto.randomUUID();
    return (
      await request("/v1/documents", { method: "POST", body: form, headers: { "Idempotency-Key": key } })
    ).json() as Promise<UploadResponse>;
  },

  /** `POST /v1/documents/{id}/versions`: 202 for a new version, 200 with `deduplicated` for identical bytes. */
  async uploadVersion(documentId: string, file: File): Promise<UploadResponse> {
    const form = new FormData();
    form.append("file", file);
    return (
      await request(`/v1/documents/${documentId}/versions`, {
        method: "POST",
        body: form,
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).json() as Promise<UploadResponse>;
  },

  async documentsMetrics(signal?: AbortSignal): Promise<DocumentsMetrics> {
    const init: RequestInit = signal ? { signal } : {};
    return (await request("/v1/metrics/documents", init)).json() as Promise<DocumentsMetrics>;
  },

  async processingMetrics(window: MetricsWindow, signal?: AbortSignal): Promise<ProcessingMetrics> {
    const init: RequestInit = signal ? { signal } : {};
    return (await request(`/v1/metrics/processing?window=${window}`, init)).json() as Promise<ProcessingMetrics>;
  },

  async chatMetrics(window: MetricsWindow, signal?: AbortSignal): Promise<ChatMetrics> {
    const init: RequestInit = signal ? { signal } : {};
    return (await request(`/v1/metrics/chat?window=${window}`, init)).json() as Promise<ChatMetrics>;
  },

  async deleteDocument(id: string): Promise<void> {
    await request(`/v1/documents/${id}`, { method: "DELETE" });
  },

  /** Exchange the API key for the session cookie. The key is not kept. */
  async login(apiKey: string): Promise<void> {
    await request("/v1/session", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
      headers: { "Content-Type": "application/json" },
    });
  },

  /** Local runs: sign in as the fixed local user. True when the server allowed it, false on 404. */
  async loginLocal(): Promise<boolean> {
    try {
      await request("/v1/session/local", { method: "POST" });
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return false;
      throw e;
    }
  },

  async logout(): Promise<void> {
    await request("/v1/session", { method: "DELETE" });
  },

  /** True when the session cookie is valid; false on 401. Other failures throw. */
  async hasSession(): Promise<boolean> {
    try {
      await request("/v1/session");
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return false;
      throw e;
    }
  },

  processingEvents(versionId: string, signal?: AbortSignal): AsyncGenerator<ProcessingEvent> {
    const opts: Parameters<typeof streamEvents<ProcessingEvent>>[1] = {
      headers: authHeaders(),
      decode: decodeProcessing,
    };
    if (signal) opts.signal = signal;
    return streamEvents(`/v1/versions/${versionId}/processing/stream`, opts);
  },
};

export function decodeProcessing(f: SseFrame): ProcessingEvent | null {
  let d: Record<string, unknown>;
  try {
    d = JSON.parse(f.data) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (f.event === "stage") return { type: "stage", ...d } as ProcessingEvent;
  if (f.event === "done") return { type: "done", ...d } as ProcessingEvent;
  if (f.event === "timeout") return { type: "timeout", ...d } as ProcessingEvent;
  return null;
}

export { SseHttpError };
