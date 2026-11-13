import { useId, useState } from "react";
import { pageLabel } from "./highlight";
import type { Citation } from "./types";
import "./source.css";

interface Props {
  /** The number shown, `[n]`. The caller numbers distinct evidence items. */
  index: number;
  citation: Citation;
  /** Shown in the hover card when known. */
  docTitle?: string;
  /** Click or Enter/Space: normally opens the source viewer. */
  onOpen: (citation: Citation) => void;
}

/** Numbered citation marker. Hover or keyboard focus shows the exact stored `cited_text`; activating opens the source. */
export function CitationMarker({ index, citation, docTitle, onOpen }: Props) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const id = useId();
  const page = pageLabel(citation.pages);
  const show = (hover || focus) && !dismissed;

  return (
    <span
      className="cite"
      onMouseEnter={() => {
        setHover(true);
        setDismissed(false);
      }}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        className="cite-btn"
        aria-label={`Citation ${index}: open source`}
        aria-describedby={show ? id : undefined}
        data-testid="citation-marker"
        onFocus={() => {
          setFocus(true);
          setDismissed(false);
        }}
        onBlur={() => setFocus(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setDismissed(true);
        }}
        onClick={() => onOpen(citation)}
      >
        [{index}]
      </button>
      {show && (
        <span role="tooltip" id={id} className="cite-card" data-testid="citation-card">
          <span className="cite-card-text">{citation.cited_text}</span>
          <span className="cite-card-meta">
            {[docTitle, page].filter(Boolean).join(" · ") || `Version ${citation.version_no}`}
          </span>
        </span>
      )}
    </span>
  );
}
