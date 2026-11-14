import type { ClarifyOption } from "../api/types";

interface Props {
  options: ClarifyOption[];
  /** Past clarifications, or a turn in flight: the chips are shown but inert. */
  disabled?: boolean;
  onPick: (option: number) => void;
}

/** The documents a clarifying question offers (S23.02). Typing a reply in the composer stays possible. */
export function ClarifyChips({ options, disabled = false, onPick }: Props) {
  return (
    <>
      <ul className="qchips" aria-label="Which document do you mean?" data-testid="clarify-chips">
        {options.map((o, i) => (
          <li key={o.document_id}>
            <button type="button" className="qchip" disabled={disabled} onClick={() => onPick(i)}>
              {o.label}
            </button>
          </li>
        ))}
      </ul>
      {!disabled && <p className="hint">Or tell me in your own words below.</p>}
    </>
  );
}
