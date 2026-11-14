import type { ConflictView, EvidenceOut } from "../api/types";
import { CitationMarker } from "../source";
import { toCitation, type Open } from "./logic";

interface Props {
  conflict: ConflictView;
  numbers: { a: number[]; b: number[] };
  onOpen: (o: Open) => void;
}

function Side({
  label,
  evidence,
  numbers,
  onOpen,
}: {
  label: string;
  evidence: EvidenceOut[];
  numbers: number[];
  onOpen: (o: Open) => void;
}) {
  const title = evidence.find((e) => e.document_title)?.document_title;
  // The same passage cited twice on one side shows once.
  const seen = new Set<number>();
  const items = evidence.flatMap((ev, i) => {
    const n = numbers[i]!;
    if (seen.has(n)) return [];
    seen.add(n);
    return [{ n, ev }];
  });
  return (
    <div className="conflict-side" data-testid="conflict-side">
      <p className="conflict-label">{title ? `${label}: ${title}` : label}</p>
      {items.map(({ n, ev }) => (
        <p className="conflict-quote" key={n}>
          {ev.cited_text}{" "}
          <CitationMarker
            index={n}
            citation={toCitation(ev)}
            {...(ev.document_title ? { docTitle: ev.document_title } : {})}
            onOpen={() => onOpen({ chunkId: ev.chunk_id, sentenceStart: ev.sentence_start, sentenceEnd: ev.sentence_end })}
          />
        </p>
      ))}
    </div>
  );
}

/** Sources that disagree: both sides side by side, each quote with its numbered citation marker. */
export function ConflictCallout({ conflict, numbers, onOpen }: Props) {
  return (
    <div className="conflict" role="group" aria-label={`Sources disagree: ${conflict.topic}`} data-testid="conflict">
      <p className="conflict-title">
        <strong>Sources disagree</strong>
        <span>{conflict.topic}</span>
      </p>
      <div className="conflict-sides">
        <Side label="One source says" evidence={conflict.side_a} numbers={numbers.a} onOpen={onOpen} />
        <Side label="Another says" evidence={conflict.side_b} numbers={numbers.b} onOpen={onOpen} />
      </div>
    </div>
  );
}
