import type { Citation } from "../source/types";

export interface Fact {
  field: string;
  value: unknown;
  status: string;
  evidence: Citation[];
}

export interface Brief {
  status: "pending" | "ready" | "failed" | "partial";
  version_id: string | null;
  document_type: string | null;
  category: string | null;
  tags: string[];
  summary: string | null;
  suggested_questions: string[];
  facts: Fact[];
}

export interface SummarySegment {
  ordinal: number;
  text: string;
  support: string;
  citations: Citation[];
}

export interface Summary {
  id: string;
  status: "pending" | "ready" | "failed";
  segments?: SummarySegment[];
  poll?: string;
  error_code?: string;
}

export const isVerified = (f: Fact): boolean => f.status === "verified";

export function groupFacts(facts: Fact[]): { field: string; facts: Fact[] }[] {
  const by = new Map<string, Fact[]>();
  for (const f of facts) by.set(f.field, [...(by.get(f.field) ?? []), f]);
  return [...by].map(([field, fs]) => ({ field, facts: fs }));
}

export function fieldLabel(field: string): string {
  const s = field.replace(/[_-]+/g, " ").trim();
  return s ? s[0]!.toUpperCase() + s.slice(1) : field;
}

export function factValue(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(factValue).join(", ");
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Key of one distinct evidence item: the chunk and its sentence range. */
export const citationKey = (c: Citation): string => `${c.chunk_id}:${c.sentence_start}-${c.sentence_end}`;

/** Numbers distinct evidence items 1..n in first-use order, so repeated citations share a marker number. */
export function numberCitations(groups: Citation[][]): number[][] {
  const seen = new Map<string, number>();
  return groups.map((g) =>
    g.map((c) => {
      const k = citationKey(c);
      if (!seen.has(k)) seen.set(k, seen.size + 1);
      return seen.get(k)!;
    }),
  );
}

/** The chat route the question chips open (the chat screen reads `q` and `doc`). */
export function chatPath(documentId: string, question: string): string {
  const qs = new URLSearchParams({ q: question, doc: documentId });
  return `/chat?${qs}`;
}

export interface Route {
  name: "library" | "document" | "chat";
  id?: string;
  params: URLSearchParams;
}

/** Parse `location.pathname + search`, with the app base (`/app/`) removed. */
export function parseRoute(pathname: string, search: string, base = "/app/"): Route {
  const params = new URLSearchParams(search);
  let p = pathname.startsWith(base.replace(/\/$/, "")) ? pathname.slice(base.length - 1) : pathname;
  p = p.replace(/\/+$/, "") || "/";
  const doc = /^\/documents\/([^/]+)$/.exec(p);
  if (doc) return { name: "document", id: decodeURIComponent(doc[1]!), params };
  if (p === "/chat") return { name: "chat", params };
  return { name: "library", params };
}
