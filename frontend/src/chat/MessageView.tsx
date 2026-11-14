import { CitationMarker } from "../source";
import { ConflictCallout } from "./ConflictCallout";
import { Feedback } from "./Feedback";
import {
  numberMessage,
  PARTIAL_REASON,
  staleInfo,
  statusLabel,
  SUPPORTED_REASON,
  toCitation,
  trustOf,
  uniqueMarkers,
  UNSUPPORTED_REASON,
  type Msg,
  type Open,
} from "./logic";

export type { Open };

interface Props {
  msg: Msg;
  docCount: number | null;
  onOpen: (o: Open) => void;
  onAsk: (q: string) => void;
  onWhy: (runId: string) => void;
  /** Ask the question of message `id` again on the current document versions. */
  onReask?: (id: string) => void;
}

export function MessageView({ msg, docCount, onOpen, onAsk, onWhy, onReask }: Props) {
  if (msg.role === "user") {
    return (
      <li className="msg msg-user" data-testid="user-message">
        <p>{msg.content}</p>
      </li>
    );
  }
  const { segments: numbers, conflicts: conflictNumbers } = numberMessage(msg.segments, msg.conflicts);
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
            <button type="button" className="btn btn-quiet" onClick={() => onReask(msg.id)}>
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
                    title={`${trust === "unsupported" ? UNSUPPORTED_REASON : PARTIAL_REASON}${s.reason ? ` ${s.reason}` : ""}`}
                    aria-label={`${trust === "unsupported" ? "Not supported by its source" : "Partly supported"}. ${
                      trust === "unsupported" ? UNSUPPORTED_REASON : PARTIAL_REASON
                    }${s.reason ? ` ${s.reason}` : ""}`}
                    data-testid={`tag-${trust}`}
                  >
                    {trust === "unsupported" ? "not supported by its source" : "partly supported"}
                  </span>
                )}
                {trust === "ok" && s.verdict === "supported" && (
                  <span className="tag tag-ok" title={SUPPORTED_REASON} data-testid="tag-supported">
                    supported
                  </span>
                )}{" "}
              </span>
            );
          })}
        </p>
      ) : (
        msg.content && !msg.streaming && <p>{msg.content}</p>
      )}
      {msg.conflicts.map((c, i) => (
        <ConflictCallout key={`${i}-${c.topic}`} conflict={c} numbers={conflictNumbers[i]!} onOpen={onOpen} />
      ))}
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
      {!msg.streaming && !failed && !msg.id.startsWith("a-") && <Feedback key={msg.id} messageId={msg.id} />}
    </li>
  );
}
