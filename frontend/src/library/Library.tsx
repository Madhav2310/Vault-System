import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, NetworkError } from "../api/client";
import type { DocumentListItem } from "../api/types";
import { ConfirmDialog } from "./ConfirmDialog";
import { DocRow } from "./DocRow";
import { Upload } from "./Upload";
import { validateFile } from "./validate";

function friendly(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return `Too many requests.${e.retryAfterS ? ` Try again in ${e.retryAfterS} s.` : " Try again shortly."}`;
    if (e.status === 413) return "That file is over the 25 MB limit.";
    if (e.status === 415) return "That file type is not supported.";
    return e.message || "Something went wrong.";
  }
  if (e instanceof NetworkError) return e.message;
  return "Something went wrong.";
}

export function Library({ onUnauthenticated }: { onUnauthenticated: () => void }) {
  const [docs, setDocs] = useState<DocumentListItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [known, setKnown] = useState<{ categories: string[]; tags: string[] }>({ categories: [], tags: [] });
  const [problems, setProblems] = useState<string[]>([]);
  const [uploading, setUploading] = useState(0);
  const [confirm, setConfirm] = useState<DocumentListItem | null>(null);
  const files = useRef(new Map<string, File>()); // document id -> file, so a failed upload can be retried
  const filtersRef = useRef({ q, category, tag });
  filtersRef.current = { q, category, tag };

  const load = useCallback(
    async (more = false, signal?: AbortSignal) => {
      const f = filtersRef.current;
      try {
        const res = await api.listDocuments(
          { q: f.q || undefined, category: f.category || undefined, tag: f.tag || undefined, ...(more && cursor ? { cursor } : {}) },
          signal,
        );
        setDocs((prev) => (more && prev ? [...prev, ...res.items] : res.items));
        setCursor(res.next_cursor);
        setKnown((k) => ({
          categories: [...new Set([...k.categories, ...res.items.flatMap((d) => (d.category ? [d.category] : []))])].sort(),
          tags: [...new Set([...k.tags, ...res.items.flatMap((d) => d.tags)])].sort(),
        }));
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
        setProblems((p) => [...p, friendly(e)]);
      }
    },
    [cursor, onUnauthenticated],
  );

  // Reload when filters change; search is debounced.
  useEffect(() => {
    const ctl = new AbortController();
    const t = setTimeout(() => void load(false, ctl.signal), q ? 250 : 0);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` identity changes with the cursor
  }, [q, category, tag]);

  async function uploadAll(picked: File[]) {
    const msgs: string[] = [];
    const valid: File[] = [];
    for (const f of picked) {
      const err = validateFile(f);
      if (err) msgs.push(err);
      else valid.push(f);
    }
    setProblems(msgs);
    if (!valid.length) return;
    setUploading((n) => n + valid.length);
    for (const f of valid) {
      try {
        const res = await api.upload(f);
        files.current.set(res.document_id, f);
        if (res.deduplicated) msgs.push(`${f.name}: already in your library.`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
        msgs.push(`${f.name}: ${friendly(e)}`);
      } finally {
        setUploading((n) => n - 1);
      }
      setProblems([...msgs]);
      await load();
    }
  }

  async function remove(doc: DocumentListItem) {
    setConfirm(null);
    try {
      await api.deleteDocument(doc.id);
      files.current.delete(doc.id);
      setDocs((d) => d?.filter((x) => x.id !== doc.id) ?? d);
    } catch (e) {
      setProblems((p) => [...p, `${doc.title}: ${friendly(e)}`]);
    }
  }

  async function retry(doc: DocumentListItem) {
    const f = files.current.get(doc.id);
    if (!f) return;
    try {
      await api.deleteDocument(doc.id);
      files.current.delete(doc.id);
      setDocs((d) => d?.filter((x) => x.id !== doc.id) ?? d);
    } catch (e) {
      setProblems((p) => [...p, `${doc.title}: ${friendly(e)}`]);
      return;
    }
    await uploadAll([f]);
  }

  const filtering = q !== "" || category !== "" || tag !== "";

  return (
    <section aria-labelledby="lib-title">
      <h1 id="lib-title">Library</h1>
      <p className="hint">Upload documents, then ask questions with exact citations.</p>
      <Upload busy={false} onFiles={(f) => void uploadAll(f)} />
      <div aria-live="polite">
        {uploading > 0 && <p className="notice">Uploading {uploading} {uploading === 1 ? "file" : "files"}…</p>}
        {problems.map((m, i) => (
          <p className="notice notice-error" role="alert" key={`${i}-${m}`}>
            {m}
          </p>
        ))}
      </div>

      <div className="toolbar" role="search">
        <label className="field grow">
          Search titles
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="field">
          Category
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {known.categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Tag
          <select value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All tags</option>
            {known.tags.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
      </div>

      {docs === null ? (
        <p className="hint" role="status">
          Loading…
        </p>
      ) : docs.length === 0 ? (
        filtering ? (
          <div className="empty">
            <h2>No matching documents</h2>
            <button
              className="btn"
              onClick={() => {
                setQ("");
                setCategory("");
                setTag("");
              }}
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div className="empty">
            <h2>Upload your first document</h2>
            <p className="hint">Drop a PDF, DOCX, TXT or MD file above. Processing starts right away.</p>
          </div>
        )
      ) : (
        <>
          <ul className="list" aria-label="Documents">
            {docs.map((d) => (
              <DocRow
                key={d.id}
                doc={d}
                canRetry={files.current.has(d.id)}
                onChanged={() => void load()}
                onRemove={setConfirm}
                onRetry={(x) => void retry(x)}
              />
            ))}
          </ul>
          {cursor && (
            <p>
              <button className="btn" onClick={() => void load(true)}>
                Load more
              </button>
            </p>
          )}
        </>
      )}

      {confirm && (
        <ConfirmDialog
          title="Remove this document?"
          body={`"${confirm.title}" and its answers' sources will be deleted. This cannot be undone.`}
          confirmLabel="Remove"
          onConfirm={() => void remove(confirm)}
          onCancel={() => setConfirm(null)}
        />
      )}
    </section>
  );
}
