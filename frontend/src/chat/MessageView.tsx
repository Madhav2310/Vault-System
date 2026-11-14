import { CitationMarker } from "../source";
import { ConflictCallout } from "./ConflictCallout";
import { CopyButton } from "./CopyButton";
import { Feedback } from "./Feedback";
import {
  FALLBACK_FOLLOWUPS,
  formatCopy,
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
  /** A turn is in flight: actions that would start another are inert. */
  busy?: boolean;
  /** The newest message in the log. */
  isLast?: boolean;
  /** Answer the last question again (offered on the last answer only). */
  onRegenerate?: () => void;
  /** Put the last question back in the composer (offered on the last question only). */
  onEdit?: () => void;
}

export function MessageView({ msg, docCount, onOpen, onAsk, onWhy, onReask, busy = false, isLast = false, onRegenerate, onEdit }: Props) {
  if (msg.role === "user") {
    return (
      <li className="msg msg-user" data-testid="user-message">
        <p>{msg.content}</p>
        {onEdit && (
          <button type="button" className="btn btn-quiet msg-edit" onClick={onEdit} data-testid="edit-question">
            Edit and resend
          </button>
        )}
      </li>
    );
  }
  const { segments: numbers, conflicts: conflictNumbers } = numberMessage(msg.segments, msg.conflicts);
  const abstain = msg.responseType === "abstain";
  const stale = staleInfo(msg);
  const failed = msg.errorCode !== null || msg.responseType === "error";
  const waiting = msg.streaming && msg.segments.length === 0 && !abstain;
  const copyText = !failed && (msg.segments.length > 0 || msg.content) ? formatCopy(msg) : "";
  const followups = msg.followups.length > 0 ? msg.followups : isLast && !failed && !abstain ? FALLBACK_FOLLOWUPS : [];

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
      {waiting && (
        <div className="skeleton" aria-hidden="true" data-testid="answer-skeleton">
          <span />
          <span />
          <span />
        </div>
      )}
      {abstain ? (
        <div className="card-abstain" data-testid="abstain-card">
          <h3>Not found in your documents</h3>
          {msg.content && <p>{msg.content}</p>}
          <p className="hint">Try one of these instead, or rephrase with more detail.</p>
        </div>
      ) : failed && msg.segments.length === 0 ? (
        <p className="notice notice-error" role="alert">
          {msg.content || "That did not finish. You can ask again, or try regenerate."}
        </p>
      ) : msg.segments.length > 0 ? (
        <p className={`answer${msg.streaming ? " answer-live" : ""}`} data-testid="answer">
          {msg.segments.map((s, i) => {
            const trust = trustOf(s);
            const markers = uniqueMarkers(s.evidence, numbers[i]!);
            // Markers wait for the verification verdict so a citation never appears before it is checked.
            const pending = msg.streaming && s.verdict === undefined;
            return (
              <span key={s.index} className={`seg seg-${trust}${msg.animate ? " seg-in" : ""}`} data-testid="segment">
                {s.text}
                <span className="marks" data-pending={pending ? "" : undefined}>
                {markers.map(({ n, ev }) => (
                  <CitationMarker
                    key={n}
                    index={n}
                    citation={toCitation(ev)}
                    {...(ev.document_title ? { docTitle: ev.document_title } : {})}
                    onOpen={() => onOpen({ chunkId: ev.chunk_id, sentenceStart: ev.sentence_start, sentenceEnd: ev.sentence_end })}
                  />
                ))}
                </span>
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
          The answer was cut short. Regenerate it, or ask again.
        </p>
      )}
      {msg.stopped && (
        <p className="stopped-note" role="status" data-testid="stopped-note">
          Stopped. What was written so far is kept. Regenerate it, or ask something else.
        </p>
      )}
      {followups.length > 0 && !msg.streaming && (
        <ul className="chips" aria-label="Follow-up questions" data-testid="followups">
          {followups.map((q) => (
            <li key={q}>
              <button type="button" className="chip" disabled={busy} onClick={() => onAsk(q)}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      )}
      {!msg.streaming && (copyText || onRegenerate || msg.runId) && (
        <div className="actions" data-testid="answer-actions">
          {copyText && <CopyButton text={copyText} />}
          {onRegenerate && (
            <button type="button" className="btn btn-quiet" onClick={onRegenerate} data-testid="regenerate">
              Regenerate
            </button>
          )}
          {msg.runId && (
            <button type="button" className="btn btn-quiet" onClick={() => onWhy(msg.runId!)}>
              Why this answer?
            </button>
          )}
        </div>
      )}
      {!msg.streaming && !failed && !msg.stopped && !msg.id.startsWith("a-") && <Feedback key={msg.id} messageId={msg.id} />}
    </li>
  );
}
