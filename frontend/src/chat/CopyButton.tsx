import { useEffect, useRef, useState } from "react";

/** Copies text to the clipboard and says so in a polite live region. */
export function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 2400);
  }

  return (
    <>
      <button type="button" className="btn btn-quiet" onClick={() => void copy()} data-testid="copy-answer">
        Copy answer
      </button>
      <span role="status" className="action-status" data-testid="copy-status">
        {state === "copied" ? "Copied with its sources." : state === "failed" ? "Could not copy. Select the text and copy it instead." : ""}
      </span>
    </>
  );
}
