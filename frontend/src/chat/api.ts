import { ApiError, CSRF_HEADERS, NetworkError } from "../api/client";
import type {
  ChatEvent,
  ChatSession,
  ChatSessionList,
  FeedbackRating,
  FeedbackReason,
  HistoryList,
  RunView,
} from "../api/types";
import { getJson } from "../source/types";
import { SseHttpError, streamEvents } from "../sse";
import { decodeChatEvent } from "./logic";

export const chatApi = {
  async listSessions(signal?: AbortSignal): Promise<ChatSession[]> {
    const init = signal ? { signal } : {};
    return (await getJson<ChatSessionList>("/v1/chat/sessions?limit=50", init)).data.items;
  },

  async createSession(documentIds: string[] | null): Promise<ChatSession> {
    const body = documentIds && documentIds.length ? { document_ids: documentIds } : {};
    return (await getJson<ChatSession>("/v1/chat/sessions", { method: "POST", body })).data;
  },

  async getSession(id: string, signal?: AbortSignal): Promise<ChatSession> {
    const init = signal ? { signal } : {};
    return (await getJson<ChatSession>(`/v1/chat/sessions/${id}`, init)).data;
  },

  /** Oldest first; follows `next_cursor` backwards so a reload restores the whole conversation (bounded). */
  async history(id: string, signal?: AbortSignal): Promise<HistoryList["items"]> {
    let all: HistoryList["items"] = [];
    let cursor: string | null = null;
    for (let page = 0; page < 10; page++) {
      const qs = new URLSearchParams({ limit: "100" });
      if (cursor) qs.set("cursor", cursor);
      const init = signal ? { signal } : {};
      const { data } = await getJson<HistoryList>(`/v1/chat/sessions/${id}/messages?${qs}`, init);
      all = [...data.items, ...all];
      cursor = data.next_cursor;
      if (!cursor) break;
    }
    return all;
  },

  async run(runId: string, signal?: AbortSignal): Promise<RunView> {
    const init = signal ? { signal } : {};
    return (await getJson<RunView>(`/v1/runs/${runId}`, init)).data;
  },

  /** Rate an answer. Idempotent per message: posting again replaces the earlier rating. */
  async feedback(messageId: string, rating: FeedbackRating, reason?: FeedbackReason, note?: string): Promise<void> {
    const body = {
      message_id: messageId,
      rating,
      ...(rating === "down" && reason ? { reason } : {}),
      ...(rating === "down" && note ? { note } : {}),
    };
    await getJson("/v1/feedback", { method: "POST", body });
  },

  /** Stream one turn. Pre-stream 404/409/422/429/503 reject with ApiError; a dropped connection with NetworkError. */
  async *send(
    sessionId: string,
    turn: ({ content: string } | { reaskOf: string } | { clarifyChoice: { messageId: string; option: number } }) & {
      /** A message of the turn this one replaces (Regenerate, Edit and resend): the server hides the old pair. */
      replaces?: string;
    },
    clientMessageId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatEvent> {
    const replaces = turn.replaces ? { replaces: turn.replaces } : {};
    const opts: Parameters<typeof streamEvents<ChatEvent>>[1] = {
      method: "POST",
      headers: CSRF_HEADERS,
      body: {
        ...replaces,
        ..."content" in turn
          ? { content: turn.content, client_message_id: clientMessageId }
          : "reaskOf" in turn
            ? { reask_of: turn.reaskOf, client_message_id: clientMessageId }
            : {
                clarify_choice: { message_id: turn.clarifyChoice.messageId, option: turn.clarifyChoice.option },
                client_message_id: clientMessageId,
              },
      },
      decode: decodeChatEvent,
    };
    if (signal) opts.signal = signal;
    try {
      yield* streamEvents(`/v1/chat/sessions/${sessionId}/messages`, opts);
    } catch (e) {
      if (e instanceof SseHttpError) throw await toApiError(e.response);
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new NetworkError("Network error: the connection was lost.");
    }
  },
};

async function toApiError(res: Response): Promise<ApiError> {
  let body: { code?: string; detail?: string | null; title?: string; retryable?: boolean } = {};
  try {
    body = (await res.json()) as typeof body;
  } catch {
    /* non-JSON body */
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
