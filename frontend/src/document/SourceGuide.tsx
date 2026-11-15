import { CitationMarker } from "../source/CitationMarker";
import type { Citation } from "../source/types";
import { chatPath, fieldLabel, factValue, groupFacts, isVerified, numberGuide, type Brief } from "./logic";
import { hrefFor, linkClick } from "./nav";

interface Props {
  documentId: string;
  title: string;
  brief: Brief;
  onOpen: (c: Citation) => void;
}

/** Source guide card: summary, type, tags, key facts with citations, and suggested-question chips. */
export function SourceGuide({ documentId, title, brief, onOpen }: Props) {
  const groups = groupFacts(brief.facts);
  const tone = brief.tone ?? null;
  const numbers = numberGuide(groups, tone?.evidence ?? []);

  return (
    <section className="guide" aria-labelledby="guide-h" data-testid="source-guide">
      <h2 id="guide-h">Source guide</h2>
      {brief.status === "partial" && (
        <p className="notice">Some parts of this guide could not be produced.</p>
      )}
      {brief.summary && <p className="guide-summary">{brief.summary}</p>}
      <p className="row-meta">
        {brief.document_type && <span className="chip">{brief.document_type}</span>}
        {brief.tags.map((t) => (
          <span className="chip" key={t}>
            {t}
          </span>
        ))}
      </p>

      {tone && (
        <p className="guide-tone" data-testid="tone">
          <strong>Tone:</strong> {tone.label}
          {tone.note && ` \u2014 ${tone.note}`}
          {tone.status !== "verified" && (
            <>
              {" "}
              <span className="chip chip-unverified">unverified</span>
            </>
          )}
          {tone.evidence.map((c, j) => (
            <CitationMarker key={j} index={numbers.tone[j]!} citation={c} docTitle={title} onOpen={onOpen} />
          ))}
        </p>
      )}

      {groups.length > 0 && (
        <>
          <h3>Key facts</h3>
          <dl className="facts">
            {groups.map((g) => (
              <div className="fact-group" key={g.field}>
                <dt>{fieldLabel(g.field)}</dt>
                {g.facts.map((f, i) => (
                  <dd key={i} data-testid="fact" data-status={f.status}>
                    <span>{factValue(f.value)}</span>
                    {!isVerified(f) && (
                      <span className="chip chip-unverified" data-testid="unverified">
                        unverified
                      </span>
                    )}
                    {f.evidence.map((c, j) => (
                      <CitationMarker
                        key={j}
                        index={numbers.of(f)[j]!}
                        citation={c}
                        docTitle={title}
                        onOpen={onOpen}
                      />
                    ))}
                  </dd>
                ))}
              </div>
            ))}
          </dl>
        </>
      )}

      {brief.suggested_questions.length > 0 && (
        <>
          <h3>Ask about this document</h3>
          <ul className="chips" aria-label="Suggested questions">
            {brief.suggested_questions.map((q) => {
              const path = chatPath(documentId, q);
              return (
                <li key={q}>
                  <a className="chip chip-link" href={hrefFor(path)} onClick={linkClick(path)}>
                    {q}
                  </a>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
