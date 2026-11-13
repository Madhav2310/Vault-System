/** Pure chat logic: SSE event decoding, the turn reducer, marker numbering, labels. No React, no fetch. */
import type { SseFrame } from "../sse";
import type { ChatEvent, ChatStage, EvidenceOut, HistoryMessage } from "../api/types";

export const EVENT_TYPES = ["run.started", "status", "segment", "verification", "followups", "done", "error"] as const;

/** Frame -> typed event. Unknown event names and bad JSON are skipped (forward compatible). */
export function decodeChatEvent(f: SseFrame): ChatEvent | null {
  if (!(EVENT_TYPES as readonly string[]).includes(f.event)) return null;
  try {
    const d = JSON.parse(f.data) as Record<string, unknown>;
    return { ...d, type: f.event } as ChatEvent;
  } catch {
    return null;
  }
}

export interface Seg {
  index: number;
  text: string;
  support: string;
  evidence: EvidenceOut[];
  verdict?: string;
}

/** One chat message as the UI shows it, live or restored from history. */
export interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  responseType: string | null;
  segments: Seg[];
  followups: string[];
  runId: string | null;
  staleness: Record<string, unknown> | null;
  /** True while the turn is still streaming. */
  streaming: boolean;
  stage: ChatStage | null;
  /** Set when the stream ended with an `error` event and no `done`. */
  errorCode: string | null;
  reasonCodes: string[];
}

export function pendingAssistant(id: string): Msg {
  return {
    id,
    role: "assistant",
    content: "",
    responseType: null,
    segments: [],
    followups: [],
    runId: null,
    staleness: null,
    streaming: true,
    stage: null,
    errorCode: null,
    reasonCodes: [],
  };
}

export function userMsg(id: string, content: string): Msg {
  return { ...pendingAssistant(id), role: "user", content, streaming: false };
}

/** Apply one event to the in-flight assistant message. Pure; replaying a transcript is deterministic. */
export function reduceTurn(m: Msg, e: ChatEvent): Msg {
  switch (e.type) {
    case "run.started":
      return { ...m, runId: e.run_id };
    case "status":
      return { ...m, runId: m.runId ?? e.run_id, stage: e.stage };
    case "segment": {
      const seg: Seg = { index: e.index, text: e.text, support: e.support, evidence: e.evidence };
      // Idempotent on index: a replayed segment replaces itself instead of duplicating.
      const rest = m.segments.filter((s) => s.index !== e.index);
      const segments = [...rest, seg].sort((a, b) => a.index - b.index);
      return { ...m, runId: m.runId ?? e.run_id, segments };
    }
    case "verification": {
      const v = new Map(e.verdicts.map((x) => [x.index, x.verdict]));
      return { ...m, segments: m.segments.map((s) => (v.has(s.index) ? { ...s, verdict: v.get(s.index)! } : s)) };
    }
    case "followups":
      return { ...m, followups: e.questions };
    case "done": {
      const isError = e.response_type === "error";
      return {
        ...m,
        id: e.message_id,
        runId: e.run_id,
        content: e.content,
        responseType: e.response_type,
        reasonCodes: e.reason_codes,
        // An error after streamed segments replaces what was shown with the stored content.
        segments: isError ? [] : m.segments,
        followups: isError ? [] : m.followups,
        streaming: false,
        stage: null,
      };
    }
    case "error":
      return { ...m, streaming: false, stage: null, errorCode: e.code };
  }
}

export function fromHistory(h: HistoryMessage): Msg {
  return {
    id: h.id,
    role: h.role === "user" ? "user" : "assistant",
    content: h.content,
    responseType: h.response_type,
    segments: (h.segments ?? []).map((s) => ({
      index: s.ordinal,
      text: s.text,
      support: s.support,
      evidence: s.evidence ?? [],
    })),
    followups: h.followups ?? [],
    runId: h.run_id,
    staleness: h.staleness,
    streaming: false,
    stage: null,
    errorCode: null,
    reasonCodes: [],
  };
}

/** Plain-language status line. `docCount` is the scope size when the session is scoped. */
export function statusLabel(stage: ChatStage | null, docCount: number | null = null): string {
  switch (stage) {
    case null:
      return "Starting…";
    case "analyzing":
      return "Understanding your question…";
    case "retrieving":
      return "Searching your documents…";
    case "reading_documents":
      return docCount && docCount > 0
        ? `Reading ${docCount} document${docCount === 1 ? "" : "s"}…`
        : "Reading your documents…";
    case "checking_evidence":
      return "Checking the evidence…";
    case "writing":
      return "Writing…";
    case "verifying":
      return "Checking citations…";
    case "saving":
      return "Saving…";
  }
}

export const evidenceKey = (e: EvidenceOut): string => `${e.chunk_id}:${e.sentence_start}-${e.sentence_end}`;

/** Numbers distinct evidence items 1..n in first-use order across a message; repeats share a number. */
export function numberEvidence(segments: { evidence: EvidenceOut[] }[]): number[][] {
  const seen = new Map<string, number>();
  return segments.map((s) =>
    s.evidence.map((e) => {
      const k = evidenceKey(e);
      if (!seen.has(k)) seen.set(k, seen.size + 1);
      return seen.get(k)!;
    }),
  );
}

/** A segment's markers with duplicates (same item cited twice in one segment) collapsed. */
export function uniqueMarkers(evidence: EvidenceOut[], numbers: number[]): { n: number; ev: EvidenceOut }[] {
  const out = new Map<number, EvidenceOut>();
  evidence.forEach((ev, i) => {
    if (!out.has(numbers[i]!)) out.set(numbers[i]!, ev);
  });
  return [...out].map(([n, ev]) => ({ n, ev }));
}

export type Trust = "ok" | "uncited" | "unsupported" | "partial";

export function trustOf(s: Seg): Trust {
  if (s.verdict === "unsupported") return "unsupported";
  if (s.support === "none") return "uncited";
  if (s.verdict === "partial") return "partial";
  return "ok";
}

export const UNSUPPORTED_REASON =
  "The cited passage does not back this statement. Open the source to check it yourself.";
export const PARTIAL_REASON = "The cited passage backs only part of this statement.";

/** Stale banner data when the message says so; the backend shape is not pinned, so read defensively. */
export function staleInfo(m: Msg): { versionNo: number | null } | null {
  const s = m.staleness;
  if (!s || s["stale"] === false) return null;
  const n = s["current_version_no"] ?? s["version_no"];
  return { versionNo: typeof n === "number" ? n : null };
}

export interface SendFailure {
  message: string;
  /** The same client_message_id is safe to reuse on retry. */
  reuseId: boolean;
}

export type FailureKind =
  | { http: number; code: string; retryAfterS: number | null }
  | { network: true }
  | { stream: string };

/** Friendly text for a pre-stream HTTP error, a mid-stream `error` event, or a dropped connection. */
export function describeFailure(kind: FailureKind): SendFailure {
  if ("network" in kind) {
    return { message: "Could not reach the server. Check your connection and try again. Your question is kept.", reuseId: true };
  }
  if ("stream" in kind) {
    return { message: "Something went wrong while answering. You can ask again. Your question is kept.", reuseId: true };
  }
  switch (kind.http) {
    case 409:
      return {
        message: "Another question is still being answered in this chat. Wait for it to finish, then send again.",
        reuseId: false,
      };
    case 429:
      return {
        message: `You are asking too quickly.${
          kind.retryAfterS ? ` Try again in ${retryTime(kind.retryAfterS)}.` : " Try again shortly."
        } Your question is kept.`,
        reuseId: false,
      };
    case 503:
      return { message: "The answering service is busy or unavailable. Try again in a moment. Your question is kept.", reuseId: true };
    case 404:
      return { message: "That chat or one of its documents was not found.", reuseId: false };
    default:
      return { message: "Something went wrong. Try again. Your question is kept.", reuseId: false };
  }
}

export function retryTime(s: number): string {
  if (s < 90) return `${Math.ceil(s)} second${Math.ceil(s) === 1 ? "" : "s"}`;
  return `${Math.ceil(s / 60)} minutes`;
}

export function sessionLabel(title: string | null, updatedAt: string): string {
  if (title && title.trim()) return title;
  const d = new Date(updatedAt);
  return Number.isNaN(d.getTime()) ? "Untitled chat" : `Chat from ${d.toLocaleDateString()}`;
}

/** `/chat?s=SESSION` path used once a session exists. */
export const sessionPath = (id: string): string => `/chat?${new URLSearchParams({ s: id })}`;
