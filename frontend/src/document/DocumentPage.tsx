import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { SourceViewer } from "../source/SourceViewer";
import { friendlyError, getJson, type Citation } from "../source/types";
import { CustomSummary } from "./CustomSummary";
import type { Brief } from "./logic";
import { hrefFor, linkClick } from "./nav";
import { SourceGuide } from "./SourceGuide";
import "./document.css";

const BRIEF_POLL_MS = 3000;
const BRIEF_POLL_MAX = 40;

export function DocumentPage({ id, onUnauthenticated }: { id: string; onUnauthenticated: () => void }) {
  const [title, setTitle] = useState<string | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Citation | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setTitle(null);
    setBrief(null);
    setError(null);
    setOpen(null);

    const fail = (e: unknown) => {
      if (e instanceof DOMException && e.name === "AbortError") return;
      if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
      setError(friendlyError(e, "This document"));
    };

    getJson<{ title: string }>(`/v1/documents/${id}`, { signal: ctl.signal })
      .then(({ data }) => setTitle(data.title))
      .catch(fail);

    (async () => {
      for (let i = 0; i < BRIEF_POLL_MAX; i++) {
        const { data } = await getJson<Brief>(`/v1/documents/${id}/brief`, { signal: ctl.signal });
        setBrief(data);
        if (data.status !== "pending") return;
        await new Promise((r) => setTimeout(r, BRIEF_POLL_MS));
        if (ctl.signal.aborted) return;
      }
    })().catch(fail);

    return () => ctl.abort();
  }, [id, onUnauthenticated]);

  const shownTitle = title ?? "Document";

  return (
    <div className="doc-page">
      <p>
        <a href={hrefFor("/")} onClick={linkClick("/")}>
          Back to library
        </a>
      </p>
      <h1 data-testid="doc-title">{shownTitle}</h1>
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
      <div className={open ? "doc-grid doc-grid-open" : "doc-grid"}>
        <div className="doc-main">
          {!brief && !error && <p className="hint">Loading the source guide…</p>}
          {brief?.status === "pending" && <p className="hint">The source guide is being prepared…</p>}
          {brief?.status === "failed" && (
            <p className="notice">A source guide could not be produced for this document. You can still ask questions about it.</p>
          )}
          {brief && (brief.status === "ready" || brief.status === "partial") && (
            <SourceGuide documentId={id} title={shownTitle} brief={brief} onOpen={setOpen} />
          )}
          <CustomSummary documentId={id} title={shownTitle} onOpen={setOpen} />
        </div>
        {open && (
          <div className="doc-side">
            <SourceViewer
              key={`${open.chunk_id}:${open.sentence_start}-${open.sentence_end}`}
              chunkId={open.chunk_id}
              sentenceStart={open.sentence_start}
              sentenceEnd={open.sentence_end}
              onClose={() => setOpen(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
