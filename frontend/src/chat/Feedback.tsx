import { useState } from "react";
import { hrefFor, linkClick } from "../document/nav";
import { chatApi } from "./api";
import { REASON_CHIPS } from "./logic";

const PICK_SCOPE = "/chat?pick=1";

/** Thumbs on one answer. A rating posts at once; a reason chip for "down" is optional and replaces the rating. */
export function Feedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [chip, setChip] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function rate(next: "up" | "down", key?: string) {
    const c = REASON_CHIPS.find((x) => x.key === key);
    setBusy(true);
    setError(null);
    try {
      await chatApi.feedback(messageId, next, c?.reason, c?.note);
      setRating(next);
      setChip(c?.key ?? null);
    } catch {
      setError("That did not save. Give it another try.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="feedback" data-testid="feedback">
      <span className="feedback-label" id={`fb-${messageId}`}>
        Was this helpful?
      </span>
      <div role="group" aria-labelledby={`fb-${messageId}`} className="feedback-buttons">
        <button
          type="button"
          className="btn btn-quiet"
          aria-pressed={rating === "up"}
          disabled={busy}
          onClick={() => void rate("up")}
          data-testid="feedback-up"
        >
          Helpful
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          aria-pressed={rating === "down"}
          disabled={busy}
          onClick={() => void rate("down", chip ?? undefined)}
          data-testid="feedback-down"
        >
          Not helpful
        </button>
      </div>
      {rating === "down" && (
        <div className="feedback-reasons" role="group" aria-label="What was off?" data-testid="feedback-reasons">
          {REASON_CHIPS.map((c) => (
            <button
              key={c.key}
              type="button"
              className="chip chip-small"
              aria-pressed={chip === c.key}
              disabled={busy}
              onClick={() => void rate("down", c.key)}
              data-testid={`reason-${c.key}`}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
      {rating === "down" && chip === "wrong" && (
        <p className="feedback-nudge" data-testid="wrong-doc-nudge">
          Wrong document?{" "}
          <a href={hrefFor(PICK_SCOPE)} onClick={linkClick(PICK_SCOPE)}>
            Choose which documents to search
          </a>
        </p>
      )}
      <span role="status" className="feedback-thanks" data-testid="feedback-status">
        {rating ? "Thanks. Your feedback is saved." : ""}
      </span>
      {error && (
        <span className="notice notice-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
