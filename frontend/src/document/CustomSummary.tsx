import { useEffect, useRef, useState } from "react";
import { CitationMarker } from "../source/CitationMarker";
import { friendlyError, getJson, type Citation } from "../source/types";
import { numberCitations, type Summary } from "./logic";

const POLL_MS = 1000;
const POLL_MAX = 90;

interface Props {
  documentId: string;
  title: string;
  onOpen: (c: Citation) => void;
}

/** Custom summary: length, tone and focus. Shows "pending" while it is produced, then the cited segments. */
export function CustomSummary({ documentId, title, onOpen }: Props) {
  const [length, setLength] = useState("medium");
  const [tone, setTone] = useState("neutral");
  const [focus, setFocus] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ctl = useRef<AbortController | null>(null);

  useEffect(() => () => ctl.current?.abort(), []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const body: Record<string, string> = { length, tone };
      if (focus.trim()) body.focus = focus.trim();
      let { data } = await getJson<Summary>(`/v1/documents/${documentId}/summaries`, {
        method: "POST",
        body,
        signal: c.signal,
      });
      setSummary(data);
      for (let i = 0; data.status === "pending" && i < POLL_MAX; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (c.signal.aborted) return;
        ({ data } = await getJson<Summary>(`/v1/documents/${documentId}/summaries/${data.id}`, { signal: c.signal }));
        setSummary(data);
      }
      if (data.status === "pending") setError("The summary is taking longer than expected. Try again in a moment.");
      if (data.status === "failed") setError("The summary could not be produced. Try again.");
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(friendlyError(err, "The summary"));
    } finally {
      if (ctl.current === c) setBusy(false);
    }
  }

  const segments = summary?.status === "ready" ? (summary.segments ?? []) : [];
  const numbers = numberCitations(segments.map((s) => s.citations));

  return (
    <section className="custom-summary" aria-labelledby="summary-h">
      <h2 id="summary-h">Custom summary</h2>
      <form onSubmit={submit} className="summary-form">
        <label className="field">
          Length
          <select value={length} onChange={(e) => setLength(e.target.value)}>
            <option value="short">Short (about 80 words)</option>
            <option value="medium">Medium (about 200 words)</option>
            <option value="long">Long (about 450 words)</option>
          </select>
        </label>
        <label className="field">
          Tone
          <select value={tone} onChange={(e) => setTone(e.target.value)}>
            <option value="neutral">Neutral</option>
            <option value="executive">Executive</option>
            <option value="plain">Plain language</option>
          </select>
        </label>
        <label className="field grow">
          Focus (optional)
          <input value={focus} maxLength={200} placeholder="For example: termination and fees" onChange={(e) => setFocus(e.target.value)} />
        </label>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Summarising…" : "Summarise"}
        </button>
      </form>

      <div aria-live="polite" data-testid="summary-status">
        {summary?.status === "pending" && (
          <p className="hint" data-testid="summary-pending">
            Pending: writing your summary…
          </p>
        )}
      </div>
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
      {segments.length > 0 && (
        <div className="summary-body" data-testid="summary-ready">
          {segments.map((s, i) => (
            <p key={s.ordinal} data-testid="summary-segment" data-support={s.support}>
              {s.text}
              {s.citations.map((c, j) => (
                <CitationMarker key={j} index={numbers[i]![j]!} citation={c} docTitle={title} onOpen={onOpen} />
              ))}
              {s.citations.length === 0 && <span className="chip">no citation</span>}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
