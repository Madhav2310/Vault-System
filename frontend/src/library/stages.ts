import type { ProcessingEvent, VersionStatus } from "../api/types";

/** Plain-language stages shown to the user, mapped from the pipeline's stage names. */
export const UI_STAGES = [
  { label: "Reading", server: ["extract"] },
  { label: "Understanding structure", server: ["quality", "chunk"] },
  { label: "Indexing", server: ["embed", "index"] },
  { label: "Writing summary", server: ["brief"] },
] as const;

export type StageState = "pending" | "active" | "done" | "failed";

export interface StageView {
  label: string;
  state: StageState;
}

export interface Progress {
  stages: StageView[];
  /** Set once the stream sent `done`. */
  done: { status: string; error_code: string | null } | null;
}

/** Fold stream events into the stage line. Idempotent under replay (catch-up then live). */
export function foldProgress(events: readonly ProcessingEvent[]): Progress {
  const ok = new Set<string>();
  let failedAt: string | null = null;
  let done: Progress["done"] = null;
  for (const e of events) {
    if (e.type === "stage") {
      if (e.status === "succeeded") ok.add(e.name);
      else if (e.status === "error") failedAt = e.name;
    } else if (e.type === "done") done = { status: e.status, error_code: e.error_code };
  }
  const success = done !== null && (done.status === "enriched" || done.status === "ready");
  let activeAssigned = false;
  const stages = UI_STAGES.map<StageView>((s) => {
    const complete = success || s.server.every((n) => ok.has(n));
    if (complete) return { label: s.label, state: "done" };
    if (failedAt && (s.server as readonly string[]).includes(failedAt)) return { label: s.label, state: "failed" };
    if (done === null && !activeAssigned && !failedAt) {
      activeAssigned = true;
      return { label: s.label, state: "active" };
    }
    return { label: s.label, state: "pending" };
  });
  return { stages, done };
}

export const TERMINAL: ReadonlySet<VersionStatus> = new Set([
  "enriched",
  "needs_ocr",
  "unsupported",
  "failed",
  "superseded",
]);

/** Statuses for which a live stream is worth opening. */
export function isLive(status: VersionStatus): boolean {
  return status === "uploaded" || status === "processing" || status === "ready";
}

export function statusLabel(status: VersionStatus): string {
  switch (status) {
    case "uploaded":
    case "processing":
      return "Processing";
    case "ready":
    case "enriched":
      return "Ready";
    case "needs_ocr":
      return "Needs OCR";
    case "unsupported":
      return "Unsupported";
    case "failed":
      return "Failed";
    case "superseded":
      return "Superseded";
  }
}

export function isFailure(status: VersionStatus): boolean {
  return status === "failed" || status === "needs_ocr" || status === "unsupported";
}

const REASONS: Record<string, string> = {
  needs_ocr: "This looks like a scanned document with no text layer. Text extraction from images is not available yet.",
  unsupported_type: "This file type is not supported.",
  file_too_large: "This file is over the 25 MB limit.",
  encrypted: "This file is password-protected, so it cannot be read.",
  corrupt: "This file appears to be damaged and could not be opened.",
};

/** The failure reason in words; unknown codes get a generic sentence that still names the code. */
export function reasonInWords(status: VersionStatus, code: string | null): string {
  if (code && REASONS[code]) return REASONS[code];
  if (status === "needs_ocr") return REASONS["needs_ocr"]!;
  if (status === "unsupported") return REASONS["unsupported_type"]!;
  return "That one did not process on my side. Remove it and try uploading it again.";
}
