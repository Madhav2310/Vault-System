import { useState } from "react";
import { api, ApiError, NetworkError } from "../api/client";
import type { VersionJson } from "../api/types";
import { isFailure, isLive, reasonInWords, statusLabel } from "../library/stages";
import { useProcessing } from "../library/useProcessing";
import { validateFile } from "../library/validate";

function friendly(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 413) return "That file is over the 25 MB limit.";
    if (e.status === 415) return "That file type is not supported.";
    if (e.status === 404) return "This document no longer exists.";
    if (e.status === 429) return `Too many requests.${e.retryAfterS ? ` Try again in ${e.retryAfterS} s.` : " Try again shortly."}`;
    return e.message || "Something went wrong.";
  }
  if (e instanceof NetworkError) return e.message;
  return "Something went wrong.";
}

function VersionRow({ v, current, onChanged }: { v: VersionJson; current: boolean; onChanged: () => void }) {
  const live = isLive(v.status);
  const progress = useProcessing(v.id, live, onChanged);
  const failed = isFailure(v.status);
  const ready = v.status === "ready" || v.status === "enriched";
  const chip = failed ? "chip chip-failed" : ready ? "chip chip-ready" : "chip chip-working";
  const showStages = live && !progress.done;
  return (
    <li className="row" data-testid="version-row" data-status={v.status}>
      <div>
        <div className="row-title">
          Version {v.version_no}
          {current && " (current)"}
        </div>
        <div className="row-meta">
          <span className={chip}>{statusLabel(v.status)}</span>
          <span className="mono">{v.filename}</span>
          {v.page_count != null && (
            <span className="mono">
              {v.page_count} {v.page_count === 1 ? "page" : "pages"}
            </span>
          )}
          {v.created_at && (
            <time dateTime={v.created_at}>{new Date(v.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</time>
          )}
        </div>
        {failed && (
          <p className="notice notice-error" role="alert">
            {reasonInWords(v.status, v.error_code)}
          </p>
        )}
      </div>
      {showStages && (
        <ol className="stages" aria-label={`Processing version ${v.version_no}`}>
          {progress.stages.map((s) => (
            <li className="stage" data-state={s.state} key={s.label} aria-current={s.state === "active" ? "step" : undefined}>
              {s.label}
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}

interface Props {
  documentId: string;
  versions: VersionJson[];
  currentVersionId: string | null;
  /** Called after an upload is accepted and whenever a version finishes processing. */
  onChanged: () => void;
}

/** Version history with live status of a version being processed, and the new-version upload. */
export function Versions({ documentId, versions, currentVersionId, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const sorted = [...versions].sort((a, b) => b.version_no - a.version_no);

  async function upload(file: File) {
    const bad = validateFile(file);
    if (bad) return setMessage({ text: bad, error: true });
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.uploadVersion(documentId, file);
      setMessage(
        res.deduplicated
          ? { text: "That file is identical to the latest version, so nothing was added.", error: false }
          : { text: "New version uploaded. It replaces the current one when processing finishes.", error: false },
      );
      onChanged();
    } catch (e) {
      setMessage({ text: `${file.name}: ${friendly(e)}`, error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="versions" aria-labelledby="versions-title" data-testid="versions">
      <h2 id="versions-title">Versions</h2>
      <label className="btn" style={{ cursor: busy ? "not-allowed" : "pointer", display: "inline-block", margin: "var(--s2) 0" }}>
        {busy ? "Uploading…" : "Upload a new version"}
        <input
          type="file"
          className="sr-only"
          accept=".pdf,.docx,.txt,.md"
          aria-label="Upload a new version"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void upload(f);
          }}
          onFocus={(e) => (e.target.parentElement!.style.outline = "2px solid var(--focus)")}
          onBlur={(e) => (e.target.parentElement!.style.outline = "")}
        />
      </label>
      <div aria-live="polite">
        {message && (
          <p className={message.error ? "notice notice-error" : "notice"} role={message.error ? "alert" : undefined} data-testid="version-message">
            {message.text}
          </p>
        )}
      </div>
      {sorted.length > 0 && (
        <ul className="list" aria-label="Versions">
          {sorted.map((v) => (
            <VersionRow key={v.id} v={v} current={v.id === currentVersionId} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </section>
  );
}
