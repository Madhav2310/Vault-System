/** Pure helpers for the source viewer. Highlighting is by sentence offsets, never by string search. */

/** One entry of `chunks.sentences`: `[start, end)` character offsets (Unicode code points) into `text`. */
export type SentenceSpan = [number, number];

export interface TextPart {
  text: string;
  highlighted: boolean;
}

function asSpan(v: unknown): SentenceSpan | null {
  if (Array.isArray(v) && v.length >= 2 && Number.isInteger(v[0]) && Number.isInteger(v[1])) {
    return [v[0] as number, v[1] as number];
  }
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (Number.isInteger(o.start) && Number.isInteger(o.end)) return [o.start as number, o.end as number];
  }
  return null;
}

/**
 * Split `text` into parts, highlighting sentences `[sentenceStart, sentenceEnd)`. Offsets from the API are
 * code-point offsets (Python); they are converted so astral characters do not shift the range.
 * Returns the whole text unhighlighted when the range is empty, out of bounds or malformed.
 */
export function splitHighlight(
  text: string,
  sentences: unknown[],
  sentenceStart: number,
  sentenceEnd: number,
): TextPart[] {
  const plain: TextPart[] = text ? [{ text, highlighted: false }] : [];
  if (!(sentenceStart >= 0) || !(sentenceEnd > sentenceStart) || sentenceEnd > sentences.length) return plain;
  const first = asSpan(sentences[sentenceStart]);
  const last = asSpan(sentences[sentenceEnd - 1]);
  if (!first || !last) return plain;

  const cps = Array.from(text); // code points
  const from = first[0];
  const to = last[1];
  if (from < 0 || to <= from || to > cps.length) return plain;

  const parts: TextPart[] = [];
  const push = (s: number, e: number, highlighted: boolean) => {
    const t = cps.slice(s, e).join("");
    if (t) parts.push({ text: t, highlighted });
  };
  push(0, from, false);
  push(from, to, true);
  push(to, cps.length, false);
  return parts;
}

/** Page label for a chunk or citation. Flow documents (DOCX/TXT/MD) use 0 or nothing: no label, never "page 0". */
export function pageLabel(pages: readonly (number | null | undefined)[] | null | undefined): string | null {
  const real = (pages ?? []).filter((p): p is number => typeof p === "number" && p > 0);
  if (real.length === 0) return null;
  const lo = Math.min(...real);
  const hi = Math.max(...real);
  return lo === hi ? `Page ${lo}` : `Pages ${lo}–${hi}`;
}

export function sectionLabel(path: readonly string[] | null | undefined): string | null {
  const p = (path ?? []).filter((s) => s.trim() !== "");
  return p.length ? p.join(" › ") : null;
}
