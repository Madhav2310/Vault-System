/** Fetch-based SSE reader: EventSource cannot send POST bodies or auth headers. Generic over the event union. */

export interface SseFrame {
  event: string;
  data: string;
  id?: string;
}

/** Incremental parser for the text/event-stream wire format. Feed it decoded text chunks in any split. */
export class SseParser {
  private buf = "";
  private event = "";
  private data: string[] = [];
  private id: string | undefined;

  push(chunk: string): SseFrame[] {
    this.buf += chunk;
    const out: SseFrame[] = [];
    let nl: number;
    while ((nl = this.buf.search(/\r\n|\n|\r/)) !== -1) {
      const m = /^(\r\n|\n|\r)/.exec(this.buf.slice(nl));
      const sepLen = m ? m[0].length : 1;
      // A lone "\r" at the very end may be the first half of "\r\n": wait for more input.
      if (this.buf[nl] === "\r" && nl + 1 === this.buf.length) break;
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + sepLen);
      const frame = this.line(line);
      if (frame) out.push(frame);
    }
    return out;
  }

  private line(line: string): SseFrame | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.event = "";
        return null;
      }
      const frame: SseFrame = { event: this.event || "message", data: this.data.join("\n") };
      if (this.id !== undefined) frame.id = this.id;
      this.event = "";
      this.data = [];
      return frame;
    }
    if (line.startsWith(":")) return null; // comment, e.g. ": heartbeat"
    const i = line.indexOf(":");
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? "" : line.slice(i + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    else if (field === "id") this.id = value;
    return null;
  }
}

/** Parse a whole transcript (tests, replays). `decode` turns a frame into a typed event or null to skip it. */
export function parseTranscript<E>(text: string, decode: (f: SseFrame) => E | null): E[] {
  const out: E[] = [];
  for (const f of new SseParser().push(text.endsWith("\n\n") ? text : text + "\n\n")) {
    const e = decode(f);
    if (e !== null) out.push(e);
  }
  return out;
}

export interface StreamOptions<E> {
  method?: "GET" | "POST";
  headers?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
  decode: (f: SseFrame) => E | null;
  fetchImpl?: typeof fetch;
}

/** Thrown for a non-2xx response before the stream starts; `response` lets callers map ErrorResponse. */
export class SseHttpError extends Error {
  constructor(public readonly response: Response) {
    super(`SSE request failed: ${response.status}`);
  }
}

/** Async-iterate typed events from a streaming endpoint. Ends when the server closes the stream. */
export async function* streamEvents<E>(url: string, opts: StreamOptions<E>): AsyncGenerator<E> {
  const f = opts.fetchImpl ?? fetch;
  const headers: Record<string, string> = { Accept: "text/event-stream", ...opts.headers };
  const init: RequestInit = { method: opts.method ?? "GET", headers };
  if (opts.signal) init.signal = opts.signal;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await f(url, init);
  if (!res.ok) throw new SseHttpError(res);
  if (!res.body) return;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const frame of parser.push(dec.decode(value, { stream: true }))) {
        const e = opts.decode(frame);
        if (e !== null) yield e;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
