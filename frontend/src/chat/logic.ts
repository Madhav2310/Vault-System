/** Pure chat logic: SSE event decoding, the turn reducer, marker numbering, labels. No React, no fetch. */
import type { Citation } from "../source";
import type { SseFrame } from "../sse";
import type { ChatEvent, ChatSession, ChatStage, ConflictView, EvidenceOut, FeedbackReason, HistoryMessage, Staleness } from "../api/types";

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
  /** Support-check verdict (`supported`, `partial`, `unsupported`); absent when no check ran. */
  verdict?: string;
  /** Why the check gave its verdict, when the API provides it (history only). */
  reason?: string;
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
  staleness: Staleness | null;
  conflicts: ConflictView[];
  /** True while the turn is still streaming. */
  streaming: boolean;
  stage: ChatStage | null;
  /** Set when the stream ended with an `error` event and no `done`. */
  errorCode: string | null;
  reasonCodes: string[];
  /** The user pressed Stop: what streamed so far is kept and labelled. */
  stopped: boolean;
  /** Streamed in this session: its segments fade in. Restored history appears at once. */
  animate: boolean;
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
    conflicts: [],
    streaming: true,
    stage: null,
    errorCode: null,
    reasonCodes: [],
    stopped: false,
    animate: true,
  };
}

export function userMsg(id: string, content: string): Msg {
  return { ...pendingAssistant(id), role: "user", content, streaming: false, animate: false };
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
        conflicts: isError ? [] : (e.conflicts ?? []),
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

/** A stored verification: null or `unchecked` means no check ran, so there is no verdict to show. */
function checkOf(verification: string | null | undefined, reason: string | null | undefined): Partial<Seg> {
  if (!verification || verification === "unchecked") return {};
  return { verdict: verification, ...(reason ? { reason } : {}) };
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
      ...checkOf(s.verification, s.verification_reason),
    })),
    followups: h.followups ?? [],
    runId: h.run_id,
    staleness: h.staleness,
    conflicts: h.conflicts ?? [],
    streaming: false,
    stage: null,
    errorCode: null,
    reasonCodes: [],
    stopped: false,
    animate: false,
  };
}

/** Plain-language status line. `docCount` is the scope size when the session is scoped. */
export function statusLabel(stage: ChatStage | null, docCount: number | null = null): string {
  switch (stage) {
    case null:
      return "Getting started…";
    case "analyzing":
      return "Understanding your question…";
    case "retrieving":
      return "Searching your documents…";
    case "reading_documents":
      return docCount && docCount > 0
        ? `Reading ${docCount} document${docCount === 1 ? "" : "s"}…`
        : "Reading your documents…";
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

export interface MessageNumbers {
  segments: number[][];
  conflicts: { a: number[]; b: number[] }[];
}

/** One numbering across the answer and its conflict callouts, so the same passage keeps the same `[n]` everywhere. */
export function numberMessage(segments: { evidence: EvidenceOut[] }[], conflicts: ConflictView[]): MessageNumbers {
  const groups = [...segments.map((s) => s.evidence), ...conflicts.flatMap((c) => [c.side_a, c.side_b])];
  const all = numberEvidence(groups.map((evidence) => ({ evidence })));
  const n = segments.length;
  return {
    segments: all.slice(0, n),
    conflicts: conflicts.map((_, i) => ({ a: all[n + 2 * i]!, b: all[n + 2 * i + 1]! })),
  };
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
export const SUPPORTED_REASON = "An automatic check found that the cited passage backs this statement.";
export const PARTIAL_REASON = "The cited passage backs only part of this statement.";

/** Stale banner data (`staleness.stale`); `versionNo` is the newer current version, null when none is current. */
export function staleInfo(m: Msg): { versionNo: number | null } | null {
  const s = m.staleness;
  if (!s || !s.stale) return null;
  return { versionNo: typeof s.newer_version_no === "number" ? s.newer_version_no : null };
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
    return { message: "We could not reach the server. Check your connection and try again. Your question is kept.", reuseId: true };
  }
  if ("stream" in kind) {
    return { message: "That did not finish. Ask again whenever you are ready. Your question is kept.", reuseId: true };
  }
  if (kind.http === 429 && kind.code === "quota_exceeded") {
    return {
      message: `You have used today's allowance. It resets at midnight UTC${
        kind.retryAfterS ? ` (in about ${retryTime(kind.retryAfterS)})` : ""
      }. Your question is kept.`,
      reuseId: false,
    };
  }
  switch (kind.http) {
    case 409:
      return {
        message: "I am still answering your last question in this chat. Send this one once that finishes.",
        reuseId: false,
      };
    case 429:
      return {
        message: `Easy does it, that was a lot at once.${
          kind.retryAfterS ? ` Try again in ${retryTime(kind.retryAfterS)}.` : " Try again shortly."
        } Your question is kept.`,
        reuseId: false,
      };
    case 503:
      return { message: "The answering service is busy right now. Try again in a moment. Your question is kept.", reuseId: true };
    case 404:
      return { message: "We could not find that chat or one of its documents. Start a new chat to carry on.", reuseId: false };
    default:
      return { message: "Something did not work on our side. Try again. Your question is kept.", reuseId: false };
  }
}

export function retryTime(s: number): string {
  if (s < 90) return `${Math.ceil(s)} second${Math.ceil(s) === 1 ? "" : "s"}`;
  if (s < 5400) return `${Math.ceil(s / 60)} minutes`;
  const h = Math.round(s / 3600);
  return `${h} hour${h === 1 ? "" : "s"}`;
}

const LABEL_CHARS = 48;

/** Title, else the first question (cut at a word), else when the chat was last used, to the minute. */
export function sessionLabel(s: Pick<ChatSession, "title" | "first_question" | "updated_at">): string {
  if (s.title && s.title.trim()) return s.title;
  const q = (s.first_question ?? "").replace(/\s+/g, " ").trim();
  if (q) {
    if (q.length <= LABEL_CHARS) return q;
    const cut = q.slice(0, LABEL_CHARS);
    const space = cut.lastIndexOf(" ");
    return `${(space > LABEL_CHARS / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
  }
  const d = new Date(s.updated_at);
  return Number.isNaN(d.getTime())
    ? "Untitled chat"
    : `Chat from ${d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;
}

/** `/chat?s=SESSION` path used once a session exists. */
export const sessionPath = (id: string): string => `/chat?${new URLSearchParams({ s: id })}`;

/** A source-viewer target: one chunk and the cited sentence range. */
export interface Open {
  chunkId: string;
  sentenceStart: number;
  sentenceEnd: number;
}

export function toCitation(e: EvidenceOut): Citation {
  return {
    chunk_id: e.chunk_id,
    version_no: e.version_no ?? 0,
    pages: e.pages,
    section_path: null,
    sentence_start: e.sentence_start,
    sentence_end: e.sentence_end,
    cited_text: e.cited_text ?? "",
  };
}

/** Keep what streamed and label it stopped. A message that is not streaming is returned unchanged. */
export function markStopped(m: Msg): Msg {
  return m.streaming ? { ...m, streaming: false, stopped: true, stage: null } : m;
}

/** Remove the last question and everything after it (an answer being regenerated or an edited question). */
export function dropLastTurn(msgs: Msg[]): Msg[] {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]!.role === "user") return msgs.slice(0, i);
  return msgs;
}

/** Id of the newest assistant message the server stored (not a streaming placeholder), or null. */
export function lastServerAnswer(msgs: Msg[]): string | null {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]!;
    if (m.role === "assistant") return m.streaming || m.id.startsWith("a-") ? null : m.id;
  }
  return null;
}

export function lastUser(msgs: Msg[]): Msg | null {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]!.role === "user") return msgs[i]!;
  return null;
}

/** Within `threshold` px of the end of the page: the reader is following the answer. */
export function isNearBottom(scrollTop: number, clientHeight: number, scrollHeight: number, threshold = 48): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= threshold;
}

/** Offered after an answer that came with no follow-ups, so there is always a next step. */
export const FALLBACK_FOLLOWUPS = ["Explain that more simply", "Where in the documents is this stated?"];

/** First-run questions. The list API has no brief field, so they come from document titles. */
export function suggestionsFor(docs: { id: string; title: string }[], scope: string[]): string[] {
  const pool = scope.length ? docs.filter((d) => scope.includes(d.id)) : docs;
  const out = pool.slice(0, 2).map((d) => `What is "${d.title}" about?`);
  if (pool.length > 1 || pool.length === 0) out.push("What are the key dates and deadlines across my documents?");
  else out.push(`What are the key dates and deadlines in "${pool[0]!.title}"?`);
  return out;
}

/** Feedback reason chips. The API reason set has no "too long": it goes as `other` with a note. */
export const REASON_CHIPS: { key: string; label: string; reason: FeedbackReason; note?: string }[] = [
  { key: "wrong", label: "Wrong", reason: "wrong" },
  { key: "incomplete", label: "Incomplete", reason: "incomplete" },
  { key: "citation", label: "Bad citation", reason: "unsupported" },
  { key: "long", label: "Too long", reason: "other", note: "too long" },
];

function pagesText(p: EvidenceOut["pages"]): string {
  if (!p || p.length < 2) return "";
  return p[0] === p[1] ? `p. ${p[0]}` : `pp. ${p[0]}\u2013${p[1]}`;
}

/** Plain text for the clipboard: the answer with [n] markers, then each numbered source with its quote. */
export function formatCopy(m: Msg): string {
  if (m.segments.length === 0) return m.content;
  const { segments: nums, conflicts: cn } = numberMessage(m.segments, m.conflicts);
  const sources = new Map<number, EvidenceOut>();
  m.segments.forEach((s, i) => uniqueMarkers(s.evidence, nums[i]!).forEach(({ n, ev }) => sources.set(n, ev)));
  m.conflicts.forEach((c, i) => {
    c.side_a.forEach((ev, j) => sources.set(cn[i]!.a[j]!, ev));
    c.side_b.forEach((ev, j) => sources.set(cn[i]!.b[j]!, ev));
  });
  const body = m.segments
    .map((s, i) => `${s.text}${uniqueMarkers(s.evidence, nums[i]!).map(({ n }) => ` [${n}]`).join("")}`)
    .join(" ");
  if (sources.size === 0) return body;
  const lines = [...sources]
    .sort((a, b) => a[0] - b[0])
    .map(([n, ev]) => {
      const where = [ev.document_title ?? "Document", ev.version_no ? `v${ev.version_no}` : "", pagesText(ev.pages)].filter(Boolean).join(", ");
      return `[${n}] ${where}${ev.cited_text ? `: "${ev.cited_text}"` : ""}`;
    });
  return `${body}\n\nSources\n${lines.join("\n")}`;
}

/** A document as the scope picker sees it; `isChattable` says whether a chat can use it. */
export interface ScopeDoc {
  id: string;
  title: string;
  status: string;
  category?: string | null;
  created_at?: string | null;
}

export const isChattable = (d: Pick<ScopeDoc, "status">): boolean => d.status === "ready" || d.status === "enriched";

/** The document uploaded last that a chat can already use. Ties and missing times fall back to list order
 *  (the API lists newest first). */
export function latestDoc<T extends ScopeDoc>(docs: readonly T[]): T | undefined {
  let best: T | undefined;
  for (const d of docs) {
    if (!isChattable(d)) continue;
    if (!best || (d.created_at ?? "") > (best.created_at ?? "")) best = d;
  }
  return best;
}

/** What a new chat searches by default: just the latest document, or everything when none is usable yet. */
export function defaultScope(docs: readonly ScopeDoc[]): string[] {
  const d = latestDoc(docs);
  return d ? [d.id] : [];
}

/** Every document a chat can use. */
export function allScope(docs: readonly ScopeDoc[]): string[] {
  return docs.filter(isChattable).map((d) => d.id);
}

/** Tick or untick one document. The last ticked document cannot be unticked: a chat always has a scope. */
export function toggleDoc(scope: readonly string[], id: string, on: boolean): string[] {
  if (on) return scope.includes(id) ? [...scope] : [...scope, id];
  return scope.length <= 1 ? [...scope] : scope.filter((x) => x !== id);
}
