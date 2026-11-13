import { useId, useLayoutEffect, useRef, useState } from "react";
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
  const card = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  const page = pageLabel(citation.pages);
  const show = (hover || focus) && !dismissed;

  // Keep the card inside the viewport: a marker near the right edge shifts it left (never causes horizontal scroll).
  // Runs after every render while shown, since the marker moves when the source viewer opens beside it.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately every render; setShift is guarded so it converges
  useLayoutEffect(() => {
    if (!show || !card.current) return;
    const natural = card.current.getBoundingClientRect().right + shift;
    const next = Math.max(0, Math.ceil(natural - (document.documentElement.clientWidth - 8)));
    if (next !== shift) setShift(next);
  });

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
        <span
          role="tooltip"
          id={id}
          ref={card}
          className="cite-card"
          style={shift ? { transform: `translateX(${-shift}px)` } : undefined}
          data-testid="citation-card"
        >
          <span className="cite-card-text">{citation.cited_text}</span>
          <span className="cite-card-meta">
            {[docTitle, page].filter(Boolean).join(" · ") || `Version ${citation.version_no}`}
          </span>
        </span>
      )}
    </span>
  );
}
