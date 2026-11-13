import { describe, expect, it } from "vitest";
import type { ProcessingEvent } from "../api/types";
import { foldProgress, reasonInWords } from "./stages";
import { validateFile } from "./validate";

const ok = (name: string): ProcessingEvent => ({ type: "stage", name, status: "succeeded", attempt: 1, at: "t" });

describe("foldProgress", () => {
  it("advances the active stage as events arrive", () => {
    expect(foldProgress([]).stages.map((s) => s.state)).toEqual(["active", "pending", "pending", "pending"]);
    const p = foldProgress([ok("extract"), ok("quality"), ok("chunk")]);
    expect(p.stages.map((s) => s.state)).toEqual(["done", "done", "active", "pending"]);
  });

  it("marks everything done on a successful done event and is replay-safe", () => {
    const evs: ProcessingEvent[] = [ok("extract"), ok("extract"), { type: "done", status: "enriched", error_code: null }];
    expect(foldProgress(evs).stages.every((s) => s.state === "done")).toBe(true);
  });

  it("marks the failing stage", () => {
    const p = foldProgress([
      ok("extract"),
      { type: "stage", name: "quality", status: "error", attempt: 1, at: "t" },
      { type: "done", status: "failed", error_code: "corrupt" },
    ]);
    expect(p.stages.map((s) => s.state)).toEqual(["done", "failed", "pending", "pending"]);
    expect(reasonInWords("failed", p.done?.error_code ?? null)).toMatch(/damaged/);
  });
});

describe("validateFile", () => {
  it("rejects 30 MB and wrong types, accepts a small PDF", () => {
    expect(validateFile({ name: "big.pdf", size: 30 * 1024 * 1024 })).toMatch(/25 MB/);
    expect(validateFile({ name: "x.exe", size: 10 })).toMatch(/only PDF/);
    expect(validateFile({ name: "a.PDF", size: 10 })).toBeNull();
  });
});
