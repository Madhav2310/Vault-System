import { describe, expect, it } from "vitest";
import { parseTranscript } from "../sse";
import type { ChatEvent, EvidenceOut } from "../api/types";
import {
  decodeChatEvent,
  describeFailure,
  fromHistory,
  numberEvidence,
  numberMessage,
  pendingAssistant,
  reduceTurn,
  staleInfo,
  statusLabel,
  trustOf,
  uniqueMarkers,
  type Msg,
} from "./logic";

const ev = (chunk: string, s = 0, e = 1, text: string | null = "t"): EvidenceOut => ({
  chunk_id: chunk,
  document_id: "d",
  document_title: "Doc",
  version_no: 1,
  pages: [2, 2],
  sentence_start: s,
  sentence_end: e,
  cited_text: text,
});

const TRANSCRIPT = [
  `event: run.started\ndata: {"run_id":"r1","message_id":"u1"}`,
  `event: status\ndata: {"run_id":"r1","stage":"retrieving"}`,
  `: heartbeat`,
  `event: status\ndata: {"run_id":"r1","stage":"writing"}`,
  `event: segment\ndata: {"run_id":"r1","index":0,"text":"A.","support":"cited","evidence":[${JSON.stringify(ev("c1"))}]}`,
  `event: segment\ndata: {"run_id":"r1","index":1,"text":"B.","support":"none","evidence":[]}`,
  `event: mystery\ndata: {"x":1}`,
  `event: verification\ndata: {"run_id":"r1","verdicts":[{"index":0,"verdict":"supported"},{"index":1,"verdict":"unsupported"}]}`,
  `event: followups\ndata: {"run_id":"r1","questions":["Why?"]}`,
  `event: done\ndata: {"run_id":"r1","message_id":"a1","response_type":"answer","content":"A. B.","reason_codes":[],"usage":{"input_tokens":1,"output_tokens":2,"cost_usd":0.01},"latency_ms":5,"ttft_ms":2}`,
].join("\n\n");

const run = (events: ChatEvent[], from: Msg = pendingAssistant("tmp")) => events.reduce(reduceTurn, from);

describe("decodeChatEvent + reducer", () => {
  const events = parseTranscript(TRANSCRIPT, decodeChatEvent);

  it("skips heartbeats and unknown events", () => {
    expect(events.map((e) => e.type)).toEqual([
      "run.started",
      "status",
      "status",
      "segment",
      "segment",
      "verification",
      "followups",
      "done",
    ]);
  });

  it("folds a full turn", () => {
    const m = run(events);
    expect(m.streaming).toBe(false);
    expect(m.id).toBe("a1");
    expect(m.runId).toBe("r1");
    expect(m.segments.map((s) => [s.text, s.verdict])).toEqual([
      ["A.", "supported"],
      ["B.", "unsupported"],
    ]);
    expect(m.followups).toEqual(["Why?"]);
    expect(m.responseType).toBe("answer");
  });

  it("renders progressively: partial prefix keeps streaming with a stage", () => {
    const m = run(events.slice(0, 4));
    expect(m.streaming).toBe(true);
    expect(m.stage).toBe("writing");
    expect(m.segments).toHaveLength(1);
  });

  it("is idempotent for a replayed segment and orders by index", () => {
    const seg = (i: number, text: string): ChatEvent => ({
      type: "segment", run_id: "r", index: i, text, support: "cited", evidence: [],
    });
    const m = run([seg(1, "b"), seg(0, "a"), seg(1, "b2")]);
    expect(m.segments.map((s) => s.text)).toEqual(["a", "b2"]);
  });

  it("an error response after segments replaces what was shown", () => {
    const m = run([
      ...events.slice(0, 5),
      {
        type: "done", run_id: "r1", message_id: "a2", response_type: "error",
        content: "Something failed.", reason_codes: ["X"], usage: { input_tokens: 0, output_tokens: 0, cost_usd: 0 },
        latency_ms: null, ttft_ms: null,
      },
    ]);
    expect(m.segments).toEqual([]);
    expect(m.content).toBe("Something failed.");
  });

  it("an error event ends streaming and records the code", () => {
    const m = run([{ type: "error", run_id: "r", code: "upstream", retryable: true }]);
    expect(m.streaming).toBe(false);
    expect(m.errorCode).toBe("upstream");
  });
});

describe("marker numbering", () => {
  it("numbers distinct items in first-use order and reuses numbers for repeats", () => {
    const segs = [
      { evidence: [ev("a"), ev("b")] },
      { evidence: [ev("b"), ev("c"), ev("a", 2, 3)] },
      { evidence: [] },
    ];
    expect(numberEvidence(segs)).toEqual([[1, 2], [2, 3, 4], []]);
  });

  it("collapses a repeated item inside one segment", () => {
    const e = [ev("a"), ev("a"), ev("b")];
    const m = uniqueMarkers(e, numberEvidence([{ evidence: e }])[0]!);
    expect(m.map((x) => x.n)).toEqual([1, 2]);
  });
});

describe("trust, labels, failures", () => {
  it("trust precedence: unsupported beats uncited", () => {
    const base = { index: 0, text: "", evidence: [] };
    expect(trustOf({ ...base, support: "none", verdict: "unsupported" })).toBe("unsupported");
    expect(trustOf({ ...base, support: "none" })).toBe("uncited");
    expect(trustOf({ ...base, support: "cited", verdict: "partial" })).toBe("partial");
    expect(trustOf({ ...base, support: "cited", verdict: "supported" })).toBe("ok");
  });

  it("status labels are plain language", () => {
    expect(statusLabel("retrieving")).toBe("Searching your documents…");
    expect(statusLabel("reading_documents", 2)).toBe("Reading 2 documents…");
    expect(statusLabel("writing")).toBe("Writing…");
    expect(statusLabel("verifying")).toBe("Checking citations…");
  });

  it("429 names the retry time and 409/503/network are friendly", () => {
    expect(describeFailure({ http: 429, code: "rate_limited", retryAfterS: 30 }).message).toContain("30 seconds");
    expect(describeFailure({ http: 429, code: "rate_limited", retryAfterS: 300 }).message).toContain("5 minutes");
    expect(describeFailure({ http: 409, code: "turn_in_progress", retryAfterS: null }).message).toContain("Another question");
    expect(describeFailure({ http: 503, code: "x", retryAfterS: null }).reuseId).toBe(true);
    expect(describeFailure({ network: true }).message).toContain("Could not reach");
  });

  it("history restore keeps evidence and flags staleness", () => {
    const m = fromHistory({
      id: "m", role: "assistant", content: "A.", created_at: "", response_type: "answer", coverage_state: null,
      staleness: { stale: true, since: "2026-11-13T10:00:00Z", newer_version_no: 3 }, run_id: "r",
      segments: [{ ordinal: 0, text: "A.", support: "cited", evidence: [ev("c")] }], followups: ["Q?"],
    });
    expect(m.segments[0]!.index).toBe(0);
    expect(m.followups).toEqual(["Q?"]);
    expect(staleInfo(m)).toEqual({ versionNo: 3 });
    expect(staleInfo({ ...m, staleness: null })).toBeNull();
    expect(staleInfo({ ...m, staleness: { stale: true, since: "", newer_version_no: null } })).toEqual({ versionNo: null });
  });

  it("history restore reads the support-check verdict and reason; unchecked means none", () => {
    const seg = (ordinal: number, verification: string | null, verification_reason: string | null) => ({
      ordinal, text: "T.", support: "cited", evidence: [ev("c")], verification, verification_reason,
    });
    const m = fromHistory({
      id: "m", role: "assistant", content: "A.", created_at: "", response_type: "answer", coverage_state: null,
      staleness: null, run_id: "r",
      segments: [seg(0, "unsupported", "The passage says 60 days."), seg(1, "partial", null), seg(2, "supported", null), seg(3, "unchecked", null), seg(4, null, null)],
    });
    expect(m.segments.map((s) => s.verdict)).toEqual(["unsupported", "partial", "supported", undefined, undefined]);
    expect(m.segments[0]!.reason).toBe("The passage says 60 days.");
    expect(m.segments.map(trustOf)).toEqual(["unsupported", "partial", "ok", "ok", "ok"]);
  });

  it("conflicts come from done and from history, and share one numbering with the answer", () => {
    const conflict = { topic: "Monthly fee", side_a: [ev("c1")], side_b: [ev("c2")] };
    const done = reduceTurn(pendingAssistant("a"), {
      type: "done", run_id: "r", message_id: "m", response_type: "answer", content: "x", reason_codes: [],
      usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0 }, latency_ms: null, ttft_ms: null, conflicts: [conflict],
    });
    expect(done.conflicts).toEqual([conflict]);
    const noConflicts = reduceTurn(pendingAssistant("a"), {
      type: "done", run_id: "r", message_id: "m", response_type: "answer", content: "x", reason_codes: [],
      usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0 }, latency_ms: null, ttft_ms: null,
    });
    expect(noConflicts.conflicts).toEqual([]);
    const n = numberMessage([{ evidence: [ev("c2")] }], [conflict]);
    expect(n.segments).toEqual([[1]]);
    expect(n.conflicts).toEqual([{ a: [2], b: [1] }]);
  });

  it("quota_exceeded gets its own message with the reset time and keeps no stale id", () => {
    const f = describeFailure({ http: 429, code: "quota_exceeded", retryAfterS: 5 * 3600 });
    expect(f.message).toContain("midnight UTC");
    expect(f.message).toContain("about 5 hours");
    expect(f.message).toContain("Your question is kept");
    expect(describeFailure({ http: 429, code: "quota_exceeded", retryAfterS: null }).message).toContain("resets at midnight UTC.");
  });
});
