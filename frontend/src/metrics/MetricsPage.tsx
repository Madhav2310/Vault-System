import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { ChatMetrics, DocumentsMetrics, MetricsWindow, ProcessingMetrics } from "../api/types";
import { friendlyError } from "../source/types";
import { entries, fmtBytes, fmtMs, fmtNum, fmtRatio, fmtSeconds, fmtUsd, plural, share, words } from "./format";
import "./metrics.css";

const WINDOWS: { value: MetricsWindow; label: string }[] = [
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];

/** Pipeline stages in the order a document passes through them, in plain words. */
const STAGE_NAMES: [string, string][] = [
  ["extract", "Reading the file"],
  ["quality", "Quality check"],
  ["chunk", "Splitting into passages"],
  ["embed", "Embedding"],
  ["index", "Indexing"],
  ["brief", "Writing summary and key facts"],
];

/** The steps of one answer, in order. */
const CHAT_STEPS: [string, string][] = [
  ["analyze", "Understanding the question"],
  ["retrieve", "Finding passages"],
  ["generate", "Writing the answer"],
  ["verify", "Checking the citations"],
];

const OUTCOME_NAMES: Record<string, string> = {
  answer: "Answered with citations",
  abstain: "Said it could not find it",
  error: "Failed",
  cancelled: "Stopped",
};

const FALLBACK_NAMES: Record<string, string> = {
  no_rerank: "ranked passages without the AI reranker",
  no_embed: "searched by keywords only",
};

function Stat({ label, value, note, id, tone }: { label: string; value: string; note?: ReactNode; id?: string; tone?: "ok" | "warn" }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`} data-testid={id}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note && <p className="stat-note">{note}</p>}
    </div>
  );
}

interface BarRow {
  key: string;
  label: string;
  /** Bar length, any unit; scaled against the largest row. */
  size: number;
  text: string;
  sub?: string;
}

function Bars({ caption, note, rows }: { caption: string; note?: string; rows: BarRow[] }) {
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map((r) => r.size), 1);
  return (
    <figure className="bars">
      <figcaption>{caption}</figcaption>
      <ul>
        {rows.map((r) => (
          <li key={r.key}>
            <span className="bar-label">{r.label}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: `${Math.max(2, (r.size / max) * 100)}%` }} />
            </span>
            <span className="bar-text">
              {r.text}
              {r.sub && <span className="bar-sub"> {r.sub}</span>}
            </span>
          </li>
        ))}
      </ul>
      {note && <p className="hint">{note}</p>}
    </figure>
  );
}

function Chips({ caption, data }: { caption: string; data: Record<string, number> }) {
  const rows = entries(data).filter(([, n]) => n > 0);
  if (rows.length === 0) return null;
  return (
    <p className="chip-row">
      <span className="chip-caption">{caption}</span>
      {rows.map(([k, n]) => (
        <span className="chip" key={k}>
          {fmtNum(n)} {words(k).toLowerCase()}
        </span>
      ))}
    </p>
  );
}

function Section({ id, title, intro, error, children }: { id: string; title: string; intro: string; error: string | null; children: ReactNode }) {
  return (
    <section className="metrics-section" aria-labelledby={id} data-testid={`metrics-${id}`}>
      <h2 id={id}>{title}</h2>
      <p className="section-intro">{intro}</p>
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

const slowest = (v: number | null | undefined, f: (n: number | null) => string) => (v == null ? undefined : `Slowest 1 in 20: ${f(v)}`);

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

  const stageRows: BarRow[] = p
    ? [
        ...STAGE_NAMES.filter(([k]) => p.stage_duration_ms[k]).map(([k, label]) => [k, label] as const),
        ...Object.keys(p.stage_duration_ms)
          .filter((k) => !STAGE_NAMES.some(([n]) => n === k))
          .map((k) => [k, words(k)] as const),
      ].map(([k, label]) => {
        const s = p.stage_duration_ms[k]!;
        return {
          key: k,
          label,
          size: s.p50 ?? 0,
          text: fmtMs(s.p50),
          sub: s.p95 != null ? `(slowest ${fmtMs(s.p95)})` : undefined,
        };
      })
    : [];

  const failures = p ? Object.values(p.failures_by_error_code).reduce((a, b) => a + b, 0) : 0;

  const stepRows: BarRow[] = c?.node_latency_ms
    ? CHAT_STEPS.filter(([k]) => c.node_latency_ms?.[k]?.p50 != null).map(([k, label]) => {
        const s = c.node_latency_ms![k]!;
        return { key: k, label, size: s.p50 ?? 0, text: fmtMs(s.p50), sub: s.p95 != null ? `(slowest ${fmtMs(s.p95)})` : undefined };
      })
    : [];

  const outcomeRows: BarRow[] = c
    ? entries(c.outcomes).map(([k, n]) => ({
        key: k,
        label: OUTCOME_NAMES[k] ?? words(k),
        size: n,
        text: fmtNum(n),
        sub: `(${share(n, c.turns)})`,
      }))
    : [];

  const quota = c?.quota;
  const spent = quota?.spent_today_usd ?? null;
  const limit = quota?.limit_usd ?? null;
  const used = spent != null && limit ? Math.min(100, (spent / limit) * 100) : null;
  const fallbacks = c ? entries(c.degraded).filter(([, n]) => n > 0) : [];

  return (
    <section aria-labelledby="metrics-title">
      <h1 id="metrics-title">Metrics</h1>
      <p className="hint">
        How your library, the processing pipeline and your chats are doing. Everything here is yours alone. Library figures cover all your
        documents; processing and chat figures cover the time range.
      </p>
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

      <Section id="m-docs" title="Your library" intro="What you have uploaded and how well it was understood." error={docs.error}>
        {!d ? (
          loading
        ) : (
          <>
            <dl className="stats">
              <Stat
                label="Documents"
                value={fmtNum(d.documents)}
                id="stat-documents"
                note={entries(d.by_category).length ? entries(d.by_category).map(([k, n]) => `${n} ${words(k).toLowerCase()}`).join(", ") : "None yet"}
              />
              <Stat label="Pages" value={fmtNum(d.pages)} note={`Split into ${fmtNum(typeof d.chunks === "number" ? d.chunks : d.chunks?.total)} searchable passages`} />
              <Stat label="Storage" value={fmtBytes(d.storage_bytes)} note="Original files kept for citations" />
              <Stat
                label="Time to get ready"
                value={fmtSeconds(d.time_to_ready_seconds?.p50)}
                id="stat-ready"
                note={slowest(d.time_to_ready_seconds?.p95, fmtSeconds) ?? "From upload until you can chat"}
              />
              <Stat
                label="Facts checked"
                value={fmtRatio(d.facts?.verified_ratio)}
                id="stat-facts"
                tone={d.facts?.verified_ratio != null && d.facts.verified_ratio >= 0.9 ? "ok" : undefined}
                note={`of ${fmtNum(d.facts?.total)} key facts matched the document's own text`}
              />
              {d.needs_ocr > 0 && (
                <Stat label="Need OCR" value={fmtNum(d.needs_ocr)} tone="warn" note="Scanned pages with no readable text" />
              )}
              {d.brief_cost_usd?.total != null && (
                <Stat
                  label="Cost of summaries"
                  value={fmtUsd(d.brief_cost_usd.total)}
                  note={d.brief_cost_usd.per_document_p50 != null ? `About ${fmtUsd(d.brief_cost_usd.per_document_p50)} per document` : undefined}
                />
              )}
            </dl>
            <Chips caption="Status" data={d.by_status} />
          </>
        )}
      </Section>

      <Section id="m-processing" title="Processing" intro="How uploads move from file to something you can ask questions about." error={proc.error}>
        {!p ? (
          loading
        ) : (
          <>
            <dl className="stats">
              <Stat
                label="Uploads processed"
                value={fmtNum(p.throughput.jobs_completed)}
                id="stat-jobs"
                note={p.throughput.jobs_per_hour == null ? undefined : `${p.throughput.jobs_per_hour.toFixed(2)} per hour in this range`}
              />
              <Stat
                label="Waiting in line"
                value={p.oldest_pending_age_seconds == null ? "Nothing" : fmtSeconds(p.oldest_pending_age_seconds)}
                note={p.oldest_pending_age_seconds == null ? "No upload is waiting" : "Age of the oldest waiting upload"}
              />
              <Stat
                label="Problems"
                value={failures === 0 ? "None" : fmtNum(failures)}
                tone={failures === 0 ? "ok" : "warn"}
                note={failures === 0 ? "Every upload in this range worked" : entries(p.failures_by_error_code).map(([k, n]) => `${n} ${words(k).toLowerCase()}`).join(", ")}
              />
            </dl>
            <Bars caption="Typical time per stage" note="Bars show the typical time (median). The slowest 1 in 20 is in brackets." rows={stageRows} />
            {stageRows.length === 0 && <p className="hint">No uploads were processed in this range.</p>}
          </>
        )}
      </Section>

      <Section id="m-chat" title="Chat" intro="How fast and how costly your answers are, and what became of each question." error={chat.error}>
        {!c ? (
          loading
        ) : c.turns === 0 ? (
          <p className="hint">No answers in this range yet. Ask a question in Chat and the figures appear here.</p>
        ) : (
          <>
            <dl className="stats">
              <Stat label="Answers" value={fmtNum(c.turns)} id="stat-turns" note="Questions answered in this range" />
              <Stat
                label="Typical answer time"
                value={fmtMs(c.latency_ms?.p50)}
                id="stat-latency"
                note={slowest(c.latency_ms?.p95, fmtMs)}
              />
              <Stat
                label="First words appear after"
                value={fmtMs(c.ttft_ms?.p50)}
                id="stat-ttft"
                note={slowest(c.ttft_ms?.p95, fmtMs)}
              />
              <Stat
                label="Cost per answer"
                value={fmtUsd(c.cost_per_turn_usd?.p50)}
                note={slowest(c.cost_per_turn_usd?.p95, fmtUsd)}
              />
              <Stat
                label="Total spent"
                value={fmtUsd(c.llm.total_cost_usd)}
                id="stat-cost"
                note={`Answers and document processing${c.llm.cache_hit_ratio != null ? ` · ${fmtRatio(c.llm.cache_hit_ratio)} of prompt text reused from cache` : ""}`}
              />
              <Stat
                label="Your feedback"
                value={`${fmtNum(c.feedback.up)} helpful · ${fmtNum(c.feedback.down)} not`}
                id="stat-feedback"
                note={entries(c.feedback.by_reason).length ? entries(c.feedback.by_reason).map(([k, n]) => `${n} ${words(k).toLowerCase()}`).join(", ") : "Thumbs up or down on an answer"}
              />
            </dl>

            <div className="meter" data-testid="stat-quota">
              <div className="meter-head">
                <span>Today's allowance</span>
                <strong>
                  {quota == null || spent == null
                    ? "—"
                    : limit == null
                      ? `${fmtUsd(spent)} used, no limit`
                      : `${fmtUsd(spent)} of ${fmtUsd(limit)} used`}
                </strong>
              </div>
              {used != null && (
                <div className="meter-track" aria-hidden="true">
                  <div className={`meter-fill${used >= 80 ? " meter-warn" : ""}`} style={{ width: `${Math.max(1, used)}%` }} />
                </div>
              )}
              <p className="hint">A daily spending cap protects you from runaway cost. It resets at midnight UTC.</p>
            </div>

            <div className="bars-row">
              <Bars caption="Where the time goes" note="Typical time per step of one answer." rows={stepRows} />
              <Bars
                caption="What happened to each question"
                note={c.outcomes["abstain"] ? "Saying it could not find something is intended when the documents do not contain it. It is never a guess." : undefined}
                rows={outcomeRows}
              />
            </div>
            {fallbacks.length > 0 && (
              <p className="notice">
                {fallbacks.map(([k, n]) => `${plural(n, "answer")} ${FALLBACK_NAMES[k] ?? `used the ${words(k).toLowerCase()} fallback`}`).join("; ")}.
              </p>
            )}
          </>
        )}
      </Section>
    </section>
  );
}
