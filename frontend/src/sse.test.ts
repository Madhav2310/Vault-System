import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { decodeProcessing } from "./api/client";
import { parseTranscript, SseParser } from "./sse";

const transcript = readFileSync(new URL("./__fixtures__/sse_transcript.txt", import.meta.url), "utf8");

describe("sse reader", () => {
  it("parses the recorded processing transcript into typed events", () => {
    const events = parseTranscript(transcript, decodeProcessing);
    expect(events.map((e) => e.type)).toEqual([...Array(6).fill("stage"), "done"]);
    expect(events[0]).toMatchObject({ type: "stage", name: "extract", status: "succeeded", attempt: 1 });
    expect(events[6]).toEqual({ type: "done", status: "enriched", error_code: null });
  });

  it("is split-independent: one byte at a time gives the same frames", () => {
    const whole = new SseParser().push(transcript);
    const p = new SseParser();
    const bytewise = [...transcript].flatMap((c) => p.push(c));
    expect(bytewise).toEqual(whole);
  });

  it("skips comments, joins multi-line data and handles CRLF", () => {
    const frames = new SseParser().push(": hi\r\nevent: x\r\ndata: a\r\ndata: b\r\n\r\n");
    expect(frames).toEqual([{ event: "x", data: "a\nb" }]);
  });
});
