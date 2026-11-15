import { describe, expect, it } from "vitest";
import type { Citation } from "../source/types";
import { chatPath, factValue, groupFacts, isVerified, numberCitations, numberGuide, parseRoute, type Fact } from "./logic";

const cite = (chunk: string, s: number, e: number): Citation => ({
  chunk_id: chunk,
  version_no: 1,
  pages: [0, 0],
  section_path: [],
  sentence_start: s,
  sentence_end: e,
  cited_text: "x",
});

describe("facts", () => {
  const f = (field: string, status: string): Fact => ({ field, value: "v", status, evidence: [] });
  it("groups by field, keeping order", () => {
    const g = groupFacts([f("parties", "verified"), f("dates", "verified"), f("parties", "unverified")]);
    expect(g.map((x) => [x.field, x.facts.length])).toEqual([
      ["parties", 2],
      ["dates", 1],
    ]);
  });
  it("only status verified counts as verified", () => {
    expect(isVerified(f("a", "verified"))).toBe(true);
    expect(isVerified(f("a", "unverified"))).toBe(false);
  });
  it("renders values of any shape", () => {
    expect(factValue(["a", "b"])).toBe("a, b");
    expect(factValue(30)).toBe("30");
    expect(factValue(null)).toBe("");
  });
});

describe("numberCitations", () => {
  it("shares a number across the same chunk and range", () => {
    const a = cite("c1", 0, 1);
    const b = cite("c2", 3, 4);
    expect(numberCitations([[a], [b, a], [cite("c1", 0, 2)]])).toEqual([[1], [2, 1], [3]]);
  });
});

describe("numberGuide", () => {
  it("numbers in reading order: tone first, then facts as grouped on screen", () => {
    const fact = (field: string, ...evidence: Citation[]): Fact => ({ field, value: "v", status: "verified", evidence });
    const [a, b, c, t] = [cite("a", 0, 1), cite("b", 0, 1), cite("c", 0, 1), cite("t", 0, 1)];
    const parties1 = fact("parties", a);
    const dates = fact("dates", b);
    const parties2 = fact("parties", c, t);
    const n = numberGuide(groupFacts([parties1, dates, parties2]), [t]);
    expect(n.tone).toEqual([1]);
    expect([n.of(parties1), n.of(parties2), n.of(dates)]).toEqual([[2], [3, 1], [4]]);
  });
});

describe("routing", () => {
  it("builds the chat path with q and doc", () => {
    const p = chatPath("d1", "What is the term?");
    const u = new URL(p, "http://x");
    expect(u.pathname).toBe("/chat");
    expect(u.searchParams.get("q")).toBe("What is the term?");
    expect(u.searchParams.get("doc")).toBe("d1");
  });
  it("parses routes under the /app base", () => {
    expect(parseRoute("/app/", "").name).toBe("library");
    expect(parseRoute("/app", "").name).toBe("library");
    expect(parseRoute("/app/documents/abc", "")).toMatchObject({ name: "document", id: "abc" });
    const c = parseRoute("/app/chat", "?q=hi&doc=d1");
    expect(c.name).toBe("chat");
    expect(c.params.get("doc")).toBe("d1");
    expect(parseRoute("/app/metrics", "").name).toBe("metrics");
  });
  it("parses without a base (dev server root)", () => {
    expect(parseRoute("/documents/abc/", "", "/")).toMatchObject({ name: "document", id: "abc" });
  });
});
