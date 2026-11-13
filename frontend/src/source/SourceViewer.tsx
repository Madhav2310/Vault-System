import { useEffect, useMemo, useRef, useState } from "react";
import { pageLabel, sectionLabel, splitHighlight } from "./highlight";
import { friendlyError, getJson, type ChunkView } from "./types";
import "./source.css";

interface Props {
  chunkId: string;
  /** Sentences `[sentenceStart, sentenceEnd)` of the opened chunk are highlighted. */
  sentenceStart: number;
  sentenceEnd: number;
  onClose: () => void;
}

/** Shared source viewer: the stored chunk text with the cited sentence range highlighted by offsets. */
export function SourceViewer({ chunkId, sentenceStart, sentenceEnd, onClose }: Props) {
  const [currentId, setCurrentId] = useState(chunkId);
  const [chunk, setChunk] = useState<ChunkView | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => setCurrentId(chunkId), [chunkId]);

  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    getJson<ChunkView>(`/v1/chunks/${currentId}`, { signal: ctl.signal })
      .then(({ data }) => setChunk(data))
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setChunk(null);
        setError(friendlyError(e, "That source"));
      });
    return () => ctl.abort();
  }, [currentId]);

  const documentId = chunk?.document_id;
  useEffect(() => {
    if (!documentId) return;
    const ctl = new AbortController();
    getJson<{ title: string }>(`/v1/documents/${documentId}`, { signal: ctl.signal })
      .then(({ data }) => setTitle(data.title))
      .catch(() => setTitle(null)); // the filename is shown instead
    return () => ctl.abort();
  }, [documentId]);

  useEffect(() => {
    heading.current?.focus();
  }, [chunk?.id]);

  const highlighted = currentId === chunkId;
  const parts = useMemo(
    () => (chunk ? splitHighlight(chunk.text, chunk.sentences, highlighted ? sentenceStart : -1, sentenceEnd) : []),
    [chunk, highlighted, sentenceStart, sentenceEnd],
  );

  const page = chunk ? pageLabel([chunk.page_start, chunk.page_end]) : null;
  const section = chunk ? sectionLabel(chunk.section_path) : null;
  const prev = chunk?.prev_chunk_id ?? null;
  const next = chunk?.next_chunk_id ?? null;

  return (
    <aside
      className="source-viewer"
      aria-label="Source"
      data-testid="source-viewer"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="source-head">
        <h2 tabIndex={-1} ref={heading}>
          {chunk ? (title ?? chunk.version.filename) : "Source"}
        </h2>
        <button type="button" className="btn btn-quiet" onClick={onClose}>
          Close
        </button>
      </div>
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
      {!chunk && !error && <p className="hint">Loading the source…</p>}
      {chunk && (
        <>
          <dl className="source-meta">
            <div>
              <dt>Version</dt>
              <dd>v{chunk.version.version_no}</dd>
            </div>
            {page && (
              <div>
                <dt>Page</dt>
                <dd>{page.replace(/^Pages? /, "")}</dd>
              </div>
            )}
            {section && (
              <div>
                <dt>Section</dt>
                <dd>{section}</dd>
              </div>
            )}
          </dl>
          <p className="source-text" data-testid="source-text">
            {parts.map((p, i) =>
              p.highlighted ? (
                <mark key={i} data-testid="cited-sentence">
                  {p.text}
                </mark>
              ) : (
                <span key={i}>{p.text}</span>
              ),
            )}
          </p>
          <nav className="source-nav" aria-label="Neighbouring passages">
            <button
              type="button"
              className="btn btn-quiet"
              disabled={!prev}
              onClick={() => prev && setCurrentId(prev)}
            >
              Previous passage
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={!next}
              onClick={() => next && setCurrentId(next)}
            >
              Next passage
            </button>
          </nav>
        </>
      )}
    </aside>
  );
}
