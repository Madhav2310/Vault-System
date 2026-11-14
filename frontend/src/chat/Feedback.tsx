import { useState } from "react";
import type { FeedbackReason } from "../api/types";
import { chatApi } from "./api";

const REASONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong", label: "Wrong" },
  { value: "unsupported", label: "Not backed by the source" },
  { value: "incomplete", label: "Incomplete" },
  { value: "other", label: "Something else" },
];

/** Thumbs up/down on one answer. A rating posts at once; a reason for "down" is optional and replaces the rating. */
export function Feedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function rate(next: "up" | "down", why?: FeedbackReason) {
    setBusy(true);
    setError(null);
    try {
      await chatApi.feedback(messageId, next, why);
      setRating(next);
      setReason(why ?? null);
    } catch {
      setError("Your feedback could not be saved. Try again.");
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
          onClick={() => void rate("down", reason ?? undefined)}
          data-testid="feedback-down"
        >
          Not helpful
        </button>
      </div>
      {rating === "down" && (
        <label className="feedback-reason">
          What was wrong? (optional)
          <select
            value={reason ?? ""}
            disabled={busy}
            onChange={(e) => e.target.value && void rate("down", e.target.value as FeedbackReason)}
            data-testid="feedback-reason"
          >
            <option value="">Choose a reason</option>
            {REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <span role="status" className="feedback-thanks" data-testid="feedback-status">
        {rating ? "Thanks, your feedback was saved." : ""}
      </span>
      {error && (
        <span className="notice notice-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
