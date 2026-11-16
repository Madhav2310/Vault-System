/** Display formatting for the metrics page. All return plain text; null/undefined show as an em dash. */
const DASH = "—";

export function fmtMs(v: number | null | undefined): string {
  if (v == null) return DASH;
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
}

export function fmtSeconds(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v >= 90) return `${(v / 60).toFixed(1)} min`;
  return `${v.toFixed(1)} s`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return DASH;
  return `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`;
}

export function fmtBytes(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtNum(v: number | null | undefined): string {
  return v == null ? DASH : v.toLocaleString("en-US");
}

export function fmtRatio(v: number | null | undefined): string {
  return v == null ? DASH : `${Math.round(v * 100)}%`;
}

/** `p50 / p95` pair of one percentile object. */
export function fmtPair(p: { p50: number | null; p95: number | null } | null | undefined, f: (n: number | null) => string): string {
  return p ? `${f(p.p50)} / ${f(p.p95)}` : DASH;
}

export function entries(o: Record<string, number> | null | undefined): [string, number][] {
  return Object.entries(o ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function words(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s[0]!.toUpperCase() + s.slice(1) : key;
}

/** `n` as a share of `total`, e.g. "91%". */
export function share(n: number, total: number): string {
  return total > 0 ? `${Math.round((n / total) * 100)}%` : DASH;
}

/** "1 answer", "3 answers". */
export function plural(n: number, noun: string): string {
  return `${n.toLocaleString("en-US")} ${noun}${n === 1 ? "" : "s"}`;
}
