import { useEffect, useRef, useState } from "react";
import { friendlyError } from "../source/types";
import type { RunView } from "../api/types";
import { chatApi } from "./api";

export function WhyPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setRun(null);
    setError(null);
    chatApi
      .run(runId, ctl.signal)
      .then(setRun)
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(friendlyError(e, "That explanation"));
      });
    return () => ctl.abort();
  }, [runId]);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  const cost = run?.usage.cost_usd;
  return (
    <aside
      className="source-viewer why-panel"
      aria-label="Why this answer"
      data-testid="why-panel"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="source-head">
        <h2 tabIndex={-1} ref={heading}>
          Why this answer?
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
      {!run && !error && <p className="hint">Loading…</p>}
      {run && (
        <>
          <ul className="why-list" data-testid="why-explanation">
            {run.explanation.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          {run.sources.length > 0 && (
            <>
              <h3>Sources used</h3>
              <ul className="why-list">
                {run.sources.map((s, i) => (
                  <li key={i}>
                    {s.document_title ?? "Untitled"}
                    {s.version_no ? ` (v${s.version_no})` : ""}
                    {s.chunks_used ? `, ${s.chunks_used} passage${s.chunks_used === 1 ? "" : "s"}` : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          {run.steps.length > 0 && (
            <>
              <h3>Steps</h3>
              <ul className="why-list">
                {run.steps.map((s, i) => (
                  <li key={i}>
                    {s.node}: {s.status}
                    {s.duration_ms != null ? `, ${s.duration_ms} ms` : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="hint" data-testid="why-cost">
            {run.usage.calls ?? 0} model call{run.usage.calls === 1 ? "" : "s"}, {run.usage.input_tokens ?? 0} tokens in,{" "}
            {run.usage.output_tokens ?? 0} out{cost != null ? `, cost $${cost.toFixed(4)}` : ""}
          </p>
        </>
      )}
    </aside>
  );
}
