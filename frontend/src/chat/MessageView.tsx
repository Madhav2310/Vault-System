import { CitationMarker } from "../source";
import type { Citation } from "../source";
import type { EvidenceOut } from "../api/types";
import {
  numberEvidence,
  PARTIAL_REASON,
  staleInfo,
  statusLabel,
  trustOf,
  uniqueMarkers,
  UNSUPPORTED_REASON,
  type Msg,
} from "./logic";

export interface Open {
  chunkId: string;
  sentenceStart: number;
  sentenceEnd: number;
}

interface Props {
  msg: Msg;
  docCount: number | null;
  onOpen: (o: Open) => void;
  onAsk: (q: string) => void;
  onWhy: (runId: string) => void;
  onReask?: () => void;
}

function toCitation(e: EvidenceOut): Citation {
  return {
    chunk_id: e.chunk_id,
    version_no: e.version_no ?? 0,
    pages: e.pages,
    section_path: null,
    sentence_start: e.sentence_start,
    sentence_end: e.sentence_end,
    cited_text: e.cited_text ?? "",
  };
}

export function MessageView({ msg, docCount, onOpen, onAsk, onWhy, onReask }: Props) {
  if (msg.role === "user") {
    return (
      <li className="msg msg-user" data-testid="user-message">
        <p>{msg.content}</p>
      </li>
    );
  }
  const numbers = numberEvidence(msg.segments);
  const abstain = msg.responseType === "abstain";
  const stale = staleInfo(msg);
  const failed = msg.errorCode !== null || msg.responseType === "error";

  return (
    <li className="msg msg-assistant" data-testid="assistant-message">
      {msg.streaming && (
        <p className="status-line" role="status" aria-live="polite" data-testid="status-line">
          {statusLabel(msg.stage, docCount)}
        </p>
      )}
      {stale && (
        <p className="notice" data-testid="stale-banner">
          This answer used an older version of a document.{" "}
          {onReask && (
            <button type="button" className="btn btn-quiet" onClick={onReask}>
              {stale.versionNo ? `Re-ask on v${stale.versionNo}` : "Re-ask on the latest version"}
            </button>
          )}
        </p>
      )}
      {abstain ? (
        <div className="card-abstain" data-testid="abstain-card">
          <h3>Not found in your documents</h3>
          {msg.content && <p>{msg.content}</p>}
          <p className="hint">Try one of these instead, or rephrase with more detail.</p>
        </div>
      ) : failed && msg.segments.length === 0 ? (
        <p className="notice notice-error" role="alert">
          {msg.content || "Something went wrong while answering. You can ask again."}
        </p>
      ) : msg.segments.length > 0 ? (
        <p className="answer" data-testid="answer">
          {msg.segments.map((s, i) => {
            const trust = trustOf(s);
            const markers = uniqueMarkers(s.evidence, numbers[i]!);
            return (
              <span key={s.index} className={`seg seg-${trust}`} data-testid="segment">
                {s.text}
                {markers.map(({ n, ev }) => (
                  <CitationMarker
                    key={n}
                    index={n}
                    citation={toCitation(ev)}
                    {...(ev.document_title ? { docTitle: ev.document_title } : {})}
                    onOpen={() => onOpen({ chunkId: ev.chunk_id, sentenceStart: ev.sentence_start, sentenceEnd: ev.sentence_end })}
                  />
                ))}
                {trust === "uncited" && (
                  <span className="tag" data-testid="tag-uncited">
                    no citation
                  </span>
                )}
                {(trust === "unsupported" || trust === "partial") && (
                  <span
                    className="tag tag-warn"
                    tabIndex={0}
                    title={trust === "unsupported" ? UNSUPPORTED_REASON : PARTIAL_REASON}
                    aria-label={`${trust === "unsupported" ? "Not supported by its source" : "Partly supported"}. ${
                      trust === "unsupported" ? UNSUPPORTED_REASON : PARTIAL_REASON
                    }`}
                    data-testid={`tag-${trust}`}
                  >
                    {trust === "unsupported" ? "not supported by its source" : "partly supported"}
                  </span>
                )}{" "}
              </span>
            );
          })}
        </p>
      ) : (
        msg.content && !msg.streaming && <p>{msg.content}</p>
      )}
      {msg.errorCode && msg.segments.length > 0 && (
        <p className="notice notice-error" role="alert">
          The answer was cut short. You can ask again.
        </p>
      )}
      {msg.followups.length > 0 && !msg.streaming && (
        <ul className="chips" aria-label="Follow-up questions" data-testid="followups">
          {msg.followups.map((q) => (
            <li key={q}>
              <button type="button" className="chip" onClick={() => onAsk(q)}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      )}
      {msg.runId && !msg.streaming && (
        <p className="why">
          <button type="button" className="btn btn-quiet" onClick={() => onWhy(msg.runId!)}>
            Why this answer?
          </button>
        </p>
      )}
    </li>
  );
}
