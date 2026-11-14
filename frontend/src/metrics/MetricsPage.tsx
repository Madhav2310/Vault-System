import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { ChatMetrics, DocumentsMetrics, MetricsWindow, ProcessingMetrics } from "../api/types";
import { friendlyError } from "../source/types";
import { entries, fmtBytes, fmtMs, fmtNum, fmtPair, fmtRatio, fmtSeconds, fmtUsd, words } from "./format";
import "./metrics.css";

const WINDOWS: { value: MetricsWindow; label: string }[] = [
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];

function Stat({ label, value, id }: { label: string; value: string; id?: string }) {
  return (
    <div className="stat" data-testid={id}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Counts({ caption, data }: { caption: string; data: Record<string, number> }) {
  const rows = entries(data);
  if (rows.length === 0) return null;
  return (
    <table className="counts">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Name</th>
          <th scope="col">Count</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, n]) => (
          <tr key={k}>
            <th scope="row">{words(k)}</th>
            <td>{fmtNum(n)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Section({ id, title, error, children }: { id: string; title: string; error: string | null; children: ReactNode }) {
  return (
    <section className="metrics-section" aria-labelledby={id} data-testid={`metrics-${id}`}>
      <h2 id={id}>{title}</h2>
      {error ? (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      ) : (
        children
      )}
    </section>
  );
}

type Slot<T> = { data: T | null; error: string | null };
const empty = <T,>(): Slot<T> => ({ data: null, error: null });

export function MetricsPage({ onUnauthenticated }: { onUnauthenticated: () => void }) {
  const [win, setWin] = useState<MetricsWindow>("24h");
  const [docs, setDocs] = useState<Slot<DocumentsMetrics>>(empty);
  const [proc, setProc] = useState<Slot<ProcessingMetrics>>(empty);
  const [chat, setChat] = useState<Slot<ChatMetrics>>(empty);

  useEffect(() => {
    const ctl = new AbortController();
    const run = async <T,>(call: () => Promise<T>, set: (s: Slot<T>) => void, what: string) => {
      try {
        set({ data: await call(), error: null });
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (e instanceof ApiError && e.status === 401) return onUnauthenticated();
        set({ data: null, error: friendlyError(e, what) });
      }
    };
    void run(() => api.documentsMetrics(ctl.signal), setDocs, "Document metrics");
    void run(() => api.processingMetrics(win, ctl.signal), setProc, "Processing metrics");
    void run(() => api.chatMetrics(win, ctl.signal), setChat, "Chat metrics");
    return () => ctl.abort();
  }, [win, onUnauthenticated]);

  const d = docs.data;
  const p = proc.data;
  const c = chat.data;
  const loading = <p className="hint">Loading…</p>;

  return (
    <section aria-labelledby="metrics-title">
      <h1 id="metrics-title">Metrics</h1>
      <p className="hint">Your own documents and chats. Document figures cover everything; the others use the time range.</p>
      <label className="field metrics-window">
        Time range
        <select value={win} onChange={(e) => setWin(e.target.value as MetricsWindow)}>
          {WINDOWS.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </select>
      </label>

      <Section id="m-docs" title="Documents" error={docs.error}>
        {!d ? (
          loading
        ) : (
          <>
            <dl className="stats">
              <Stat label="Documents" value={fmtNum(d.documents)} id="stat-documents" />
              <Stat label="Pages" value={fmtNum(d.pages)} />
              <Stat label="Chunks" value={fmtNum(d.chunks?.total)} />
              <Stat label="Storage" value={fmtBytes(d.storage_bytes)} />
              <Stat label="Time to ready (p50 / p95)" value={fmtPair(d.time_to_ready_seconds, fmtSeconds)} />
              <Stat label="Facts verified" value={fmtRatio(d.facts?.verified_ratio)} />
              <Stat label="Need OCR" value={fmtNum(d.needs_ocr)} />
            </dl>
            <div className="counts-row">
              <Counts caption="By status" data={d.by_status} />
              <Counts caption="By category" data={d.by_category} />
            </div>
          </>
        )}
      </Section>

      <Section id="m-processing" title="Processing" error={proc.error}>
        {!p ? (
          loading
        ) : (
          <>
            <dl className="stats">
              <Stat label="Jobs completed" value={fmtNum(p.throughput.jobs_completed)} id="stat-jobs" />
              <Stat
                label="Jobs per hour"
                value={p.throughput.jobs_per_hour == null ? "—" : p.throughput.jobs_per_hour.toFixed(2)}
              />
              <Stat label="Oldest waiting" value={fmtSeconds(p.oldest_pending_age_seconds)} />
            </dl>
            {Object.keys(p.stage_duration_ms).length > 0 && (
              <table className="counts">
                <caption>Stage durations</caption>
                <thead>
                  <tr>
                    <th scope="col">Stage</th>
                    <th scope="col">Runs</th>
                    <th scope="col">p50</th>
                    <th scope="col">p95</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(p.stage_duration_ms).map(([k, v]) => (
                    <tr key={k}>
                      <th scope="row">{words(k)}</th>
                      <td>{fmtNum(v.count)}</td>
                      <td>{fmtMs(v.p50)}</td>
                      <td>{fmtMs(v.p95)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {Object.keys(p.failures_by_error_code).length === 0 ? (
              <p className="hint">No failures in this range.</p>
            ) : (
              <Counts caption="Failures by reason" data={p.failures_by_error_code} />
            )}
          </>
        )}
      </Section>

      <Section id="m-chat" title="Chat" error={chat.error}>
        {!c ? (
          loading
        ) : (
          <>
            <dl className="stats">
              <Stat label="Answers" value={fmtNum(c.turns)} id="stat-turns" />
              <Stat label="Latency (p50 / p95)" value={fmtPair(c.latency_ms, fmtMs)} id="stat-latency" />
              <Stat label="Time to first text (p50 / p95)" value={fmtPair(c.ttft_ms, fmtMs)} id="stat-ttft" />
              <Stat label="Cost per answer (p50 / p95)" value={fmtPair(c.cost_per_turn_usd, fmtUsd)} />
              <Stat label="Total cost" value={fmtUsd(c.llm.total_cost_usd)} id="stat-cost" />
              <Stat label="Helpful / not helpful" value={`${fmtNum(c.feedback.up)} / ${fmtNum(c.feedback.down)}`} id="stat-feedback" />
              <Stat
                label="Today's allowance"
                id="stat-quota"
                value={
                  c.quota
                    ? c.quota.limit_usd == null
                      ? `${fmtUsd(c.quota.spent_today_usd)} used, no limit`
                      : `${fmtUsd(c.quota.spent_today_usd)} of ${fmtUsd(c.quota.limit_usd)} used`
                    : "—"
                }
              />
            </dl>
            <p className="hint">The allowance resets at midnight UTC.</p>
            <div className="counts-row">
              <Counts caption="Outcomes" data={c.outcomes} />
              <Counts caption="Coverage" data={c.coverage} />
              <Counts caption="Feedback reasons" data={c.feedback.by_reason} />
            </div>
          </>
        )}
      </Section>
    </section>
  );
}
