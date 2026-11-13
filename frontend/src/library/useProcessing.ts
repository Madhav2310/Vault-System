import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ProcessingEvent } from "../api/types";
import { foldProgress, type Progress } from "./stages";

const MAX_RETRIES = 4;

/**
 * Follow the processing stream of one version. Catch-up events replay first, so folding is idempotent;
 * on a dropped connection or server `timeout` it reconnects (the server resumes from the beginning).
 * `onDone` fires once when the terminal event arrives.
 */
export function useProcessing(versionId: string, enabled: boolean, onDone: () => void): Progress {
  const [events, setEvents] = useState<ProcessingEvent[]>([]);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    let finished = false;

    (async () => {
      for (let attempt = 0; attempt <= MAX_RETRIES && !finished && !ctl.signal.aborted; attempt++) {
        if (attempt > 0) await new Promise((r) => setTimeout(r, 1000 * attempt));
        const seen: ProcessingEvent[] = [];
        try {
          for await (const e of api.processingEvents(versionId, ctl.signal)) {
            if (e.type === "timeout") break;
            seen.push(e);
            setEvents([...seen]);
            if (e.type === "done") {
              finished = true;
              doneRef.current();
              return;
            }
          }
        } catch (e) {
          if (ctl.signal.aborted) return;
          if (e instanceof Error && /401|403|404/.test(e.message)) break;
        }
      }
      if (!finished && !ctl.signal.aborted) doneRef.current(); // fall back to the list's own status
    })();

    return () => ctl.abort();
  }, [versionId, enabled]);

  return foldProgress(events);
}
