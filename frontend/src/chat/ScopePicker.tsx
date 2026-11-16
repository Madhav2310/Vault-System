import { allScope, isChattable, latestDoc, toggleDoc, type ScopeDoc } from "./logic";

const SHOWN = 2; // chips in the closed bar; the rest collapse into "+N"

function chipsFor(scope: readonly string[], docs: readonly ScopeDoc[]): { text: string; more: number } {
  const picked = scope.length ? docs.filter((d) => scope.includes(d.id)) : docs.filter(isChattable);
  const everything = picked.length > 1 && picked.length === docs.filter(isChattable).length;
  return everything ? { text: "All documents", more: 0 } : { text: picked.slice(0, SHOWN).map((d) => d.title).join("\u0000"), more: Math.max(0, picked.length - SHOWN) };
}

/** Pills for the documents a chat searches. Read-only on its own; inside the picker it is the closed bar. */
export function ScopeChips({ scope, docs }: { scope: readonly string[]; docs: readonly ScopeDoc[] }) {
  if (scope.length === 0 && docs.length === 0) return <span className="scope-pill">All documents</span>;
  const { text, more } = chipsFor(scope, docs);
  return (
    <>
      {text.split("\u0000").map((t) => (
        <span className="scope-pill" key={t} title={t}>
          {t}
        </span>
      ))}
      {more > 0 && <span className="scope-pill scope-more">+{more} more</span>}
    </>
  );
}

interface Props {
  docs: readonly ScopeDoc[];
  scope: readonly string[];
  onChange: (next: string[]) => void;
  open?: boolean;
}

/** "Chatting with": the closed bar names the documents; opened, a checklist adds or removes any of them. */
export function ScopePicker({ docs, scope, onChange, open }: Props) {
  const latest = latestDoc(docs);
  const ticked = (id: string) => scope.length === 0 || scope.includes(id);
  const count = scope.length || allScope(docs).length;
  return (
    <details className="scope" data-testid="scope-picker" open={open || undefined}>
      <summary>
        <span className="scope-label">Chatting with</span>
        <span className="scope-pills" data-testid="scope-pills">
          <ScopeChips scope={scope} docs={docs} />
        </span>
        <span className="scope-change">Change</span>
      </summary>
      <fieldset className="scope-panel">
        <legend className="sr-only">Documents to search</legend>
        <div className="scope-tools">
          <span className="scope-count" aria-live="polite">
            {count} of {allScope(docs).length || docs.length} selected
          </span>
          <button type="button" className="btn btn-quiet" onClick={() => onChange(allScope(docs))}>
            Select all
          </button>
          {latest && (
            <button type="button" className="btn btn-quiet" onClick={() => onChange([latest.id])}>
              Latest only
            </button>
          )}
        </div>
        <ul className="scope-list">
          {docs.map((d) => {
            const usable = isChattable(d);
            const only = ticked(d.id) && count <= 1;
            return (
              <li key={d.id}>
                <label className={`check scope-row${usable ? "" : " scope-row-off"}`}>
                  <input
                    type="checkbox"
                    checked={usable && ticked(d.id)}
                    disabled={!usable || only}
                    onChange={(e) => onChange(toggleDoc(scope.length ? scope : allScope(docs), d.id, e.target.checked))}
                  />
                  <span className="scope-title">{d.title}</span>
                  {d.id === latest?.id && <span className="scope-badge">Latest</span>}
                  {!usable && <span className="scope-meta">{d.status === "failed" ? "Failed" : "Processing"}</span>}
                  {usable && d.category && <span className="scope-meta">{d.category}</span>}
                </label>
              </li>
            );
          })}
        </ul>
        {count <= 1 && docs.length > 1 && <p className="hint">A chat needs at least one document.</p>}
      </fieldset>
    </details>
  );
}
