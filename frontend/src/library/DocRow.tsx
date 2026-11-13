import type { DocumentListItem } from "../api/types";
import { hrefFor, linkClick } from "../document/nav";
import { isFailure, isLive, reasonInWords, statusLabel } from "./stages";
import { useProcessing } from "./useProcessing";

interface Props {
  doc: DocumentListItem;
  canRetry: boolean;
  onChanged: () => void;
  onRemove: (doc: DocumentListItem) => void;
  onRetry: (doc: DocumentListItem) => void;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function DocRow({ doc, canRetry, onChanged, onRemove, onRetry }: Props) {
  const v = doc.version;
  const live = isLive(doc.status);
  const progress = useProcessing(v.id, live, onChanged);
  const failed = isFailure(doc.status);
  const ready = doc.status === "ready" || doc.status === "enriched";
  const chipClass = failed ? "chip chip-failed" : ready ? "chip chip-ready" : "chip chip-working";
  const showStages = live && !progress.done;

  return (
    <li className="row" data-testid="doc-row" data-status={doc.status}>
      <div>
        <div className="row-title">
          <a href={hrefFor(`/documents/${doc.id}`)} onClick={linkClick(`/documents/${doc.id}`)}>
            {doc.title}
          </a>
        </div>
        <div className="row-meta">
          <span className={chipClass}>{statusLabel(doc.status)}</span>
          {doc.category && <span>{doc.category}</span>}
          {doc.tags.map((t) => (
            <span className="chip" key={t}>
              {t}
            </span>
          ))}
          {v.page_count != null && (
            <span className="mono">
              {v.page_count} {v.page_count === 1 ? "page" : "pages"}
            </span>
          )}
          <span>
            <time dateTime={v.ready_at ?? v.created_at ?? undefined}>{fmtTime(v.ready_at ?? v.created_at)}</time>
          </span>
        </div>
        {failed && (
          <p className="notice notice-error" role="alert">
            {reasonInWords(doc.status, v.error_code)}
          </p>
        )}
      </div>
      <div className="row-actions">
        {failed && canRetry && (
          <button className="btn" onClick={() => onRetry(doc)}>
            Retry
          </button>
        )}
        <button className="btn btn-quiet btn-danger" onClick={() => onRemove(doc)} aria-label={`Remove ${doc.title}`}>
          Remove
        </button>
      </div>
      {showStages && (
        <ol className="stages" aria-label={`Processing ${doc.title}`}>
          {progress.stages.map((s) => (
            <li className="stage" data-state={s.state} key={s.label} aria-current={s.state === "active" ? "step" : undefined}>
              {s.label}
            </li>
          ))}
        </ol>
      )}
      {showStages && (
        <span className="sr-only" role="status">
          {progress.stages.find((s) => s.state === "active")?.label ?? "Waiting"}
        </span>
      )}
    </li>
  );
}
