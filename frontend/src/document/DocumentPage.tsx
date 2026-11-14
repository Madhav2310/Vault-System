import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { DocumentDetail } from "../api/types";
import { ConfirmDialog } from "../library/ConfirmDialog";
import { SourceViewer } from "../source/SourceViewer";
import { friendlyError, getJson, type Citation } from "../source/types";
import { CustomSummary } from "./CustomSummary";
import type { Brief } from "./logic";
import { hrefFor, linkClick, navigate } from "./nav";
import { SourceGuide } from "./SourceGuide";
import { Versions } from "./Versions";
import "./document.css";

const BRIEF_POLL_MS = 3000;
const BRIEF_POLL_MAX = 40;

export function DocumentPage({ id, onUnauthenticated }: { id: string; onUnauthenticated: () => void }) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Citation | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  /** Bumped when a version is added or finishes processing: reload the detail and the brief in place. */
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    setDetail(null);
    setBrief(null);
    setError(null);
    setOpen(null);
    setTick(0);
  }, [id]);

  useEffect(() => {
    const ctl = new AbortController();

    const fail = (e: unknown) => {
      if (e instanceof DOMException && e.name === "AbortError") return;
      if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
      setError(friendlyError(e, "This document"));
    };

    getJson<DocumentDetail>(`/v1/documents/${id}`, { signal: ctl.signal })
      .then(({ data }) => setDetail(data))
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
  }, [id, tick, onUnauthenticated]);

  async function remove() {
    setConfirmDelete(false);
    setDeleteError(null);
    try {
      await api.deleteDocument(id);
      navigate("/");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
      setDeleteError(e instanceof ApiError && e.status === 404 ? "This document was already removed." : "Could not delete the document. Try again.");
    }
  }

  const title = detail?.title ?? null;
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
      {deleteError && (
        <p className="notice notice-error" role="alert">
          {deleteError}
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
          {detail && (
            <Versions
              documentId={id}
              versions={detail.versions ?? []}
              currentVersionId={detail.current_version_id ?? null}
              onChanged={refresh}
            />
          )}
          <p className="doc-danger">
            <button type="button" className="btn btn-quiet btn-danger" onClick={() => setConfirmDelete(true)}>
              Delete document
            </button>
          </p>
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
      {confirmDelete && (
        <ConfirmDialog
          title="Delete this document?"
          body={`"${shownTitle}" and its answers' sources will be deleted. This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={() => void remove()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
