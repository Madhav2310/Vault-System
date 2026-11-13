import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, NetworkError } from "../api/client";
import type { ChatSession, DocumentListItem } from "../api/types";
import type { Route } from "../document/logic";
import { hrefFor, linkClick, navigate } from "../document/nav";
import { SourceViewer } from "../source";
import { friendlyError } from "../source/types";
import { chatApi } from "./api";
import {
  describeFailure,
  fromHistory,
  pendingAssistant,
  reduceTurn,
  sessionLabel,
  sessionPath,
  userMsg,
  type Msg,
} from "./logic";
import { MessageView, type Open } from "./MessageView";
import { WhyPanel } from "./WhyPanel";
import "./chat.css";

interface Props {
  route: Route;
  onUnauthenticated: () => void;
}

export function ChatPage({ route, onUnauthenticated }: Props) {
  const sessionId = route.params.get("s");
  const q = route.params.get("q");
  const docParam = route.params.get("doc");

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [docs, setDocs] = useState<DocumentListItem[]>([]);
  const [scope, setScope] = useState<string[]>(docParam ? [docParam] : []); // empty = all documents
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [source, setSource] = useState<Open | null>(null);
  const [why, setWhy] = useState<string | null>(null);

  const [activeScope, setActiveScope] = useState<string[] | null>(null);
  const abort = useRef<AbortController | null>(null);
  const pendingId = useRef<string | null>(null);
  const live = useRef<string | null>(null); // session id whose messages are local (just created or streaming)
  const autoStarted = useRef<string | null>(null);
  const logRef = useRef<HTMLOListElement>(null);

  const unauth = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) onUnauthenticated();
    },
    [onUnauthenticated],
  );

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await chatApi.listSessions());
    } catch (e) {
      unauth(e);
    }
  }, [unauth]);

  useEffect(() => {
    void refreshSessions();
    api
      .listDocuments({ limit: 100 })
      .then((l) => setDocs(l.items))
      .catch(unauth);
  }, [refreshSessions, unauth]);

  // Restore history when the URL points at a session we did not just create.
  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      setActiveScope(null);
      return;
    }
    if (live.current && live.current !== sessionId) live.current = null;
    if (live.current === sessionId) return;
    const ctl = new AbortController();
    setLoadError(null);
    setMessages([]);
    Promise.all([chatApi.getSession(sessionId, ctl.signal), chatApi.history(sessionId, ctl.signal)])
      .then(([s, h]) => {
        setActiveScope(s.document_ids);
        setMessages(h.map(fromHistory));
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        unauth(e);
        setLoadError(friendlyError(e, "That chat"));
      });
    return () => ctl.abort();
  }, [sessionId, unauth]);

  useEffect(() => {
    logRef.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" });
  }, [messages]);

  useEffect(() => () => abort.current?.abort(), []);

  const send = useCallback(
    async (content: string, sid: string) => {
      const text = content.trim();
      if (!text) return;
      setFailure(null);
      setBusy(true);
      const clientId = pendingId.current ?? crypto.randomUUID();
      pendingId.current = clientId;
      const userId = `u-${clientId}`;
      const tmp = `a-${clientId}`;
      setMessages((m) => [...m.filter((x) => x.id !== userId && x.id !== tmp), userMsg(userId, text), pendingAssistant(tmp)]);
      const ctl = new AbortController();
      abort.current = ctl;
      let ended = false;
      let streamError: string | null = null;
      try {
        for await (const e of chatApi.send(sid, text, clientId, ctl.signal)) {
          if (e.type === "done") ended = true;
          if (e.type === "error") streamError = e.code;
          setMessages((m) => m.map((x) => (x.id === tmp || (x.streaming && x.role === "assistant") ? reduceTurn(x, e) : x)));
        }
        if (!ended && !streamError) throw new NetworkError("Network error: the stream ended early.");
        if (streamError && !ended) {
          setFailure(describeFailure({ stream: streamError }).message);
          setInput(text);
          return;
        }
        pendingId.current = null;
        setInput("");
        void refreshSessions();
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        unauth(e);
        const f =
          e instanceof ApiError
            ? describeFailure({ http: e.status, code: e.code, retryAfterS: e.retryAfterS })
            : describeFailure({ network: true });
        // Pre-stream failures never produced an answer: drop the optimistic pair and keep the text.
        setMessages((m) => m.filter((x) => x.id !== userId && x.id !== tmp));
        setFailure(f.message);
        setInput(text);
        if (!f.reuseId) pendingId.current = null;
      } finally {
        setBusy(false);
      }
    },
    [refreshSessions, unauth],
  );

  const startSession = useCallback(
    async (ids: string[]): Promise<string | null> => {
      try {
        const s = await chatApi.createSession(ids.length ? ids : null);
        live.current = s.id;
        setActiveScope(s.document_ids);
        setSessions((prev) => [s, ...prev]);
        navigate(sessionPath(s.id));
        return s.id;
      } catch (e) {
        unauth(e);
        const f =
          e instanceof ApiError
            ? describeFailure({ http: e.status, code: e.code, retryAfterS: e.retryAfterS })
            : describeFailure({ network: true });
        setFailure(f.message);
        return null;
      }
    },
    [unauth],
  );

  const ask = useCallback(
    async (text: string) => {
      if (busy) return;
      let sid = sessionId;
      if (!sid) {
        setBusy(true);
        sid = await startSession(scope);
        setBusy(false);
        if (!sid) {
          setInput(text);
          return;
        }
      }
      await send(text, sid);
    },
    [busy, sessionId, scope, startSession, send],
  );

  // ?q= and ?doc= from the document page: create a scoped session and send the question once.
  useEffect(() => {
    if (!q || sessionId) return;
    const key = `${q}|${docParam ?? ""}`;
    if (autoStarted.current === key) return;
    autoStarted.current = key;
    void ask(q);
  }, [q, docParam, sessionId, ask]);

  const scopeCount = (sessionId ? activeScope : scope.length ? scope : null)?.length ?? null;
  const titleOf = (id: string) => docs.find((d) => d.id === id)?.title ?? "a document";
  const scopeText = !sessionId
    ? scope.length
      ? `Scoped to ${scope.length} document${scope.length === 1 ? "" : "s"}`
      : "Searching all documents"
    : activeScope?.length
      ? `Scoped to ${activeScope.map(titleOf).join(", ")}`
      : "Searching all documents";

  return (
    <div className="chat-layout">
      <nav className="chat-sessions" aria-label="Chats">
        <a className="btn btn-primary" href={hrefFor("/chat")} onClick={linkClick("/chat")} data-testid="new-chat">
          New chat
        </a>
        <ul>
          {sessions.map((s) => (
            <li key={s.id}>
              <a
                href={hrefFor(sessionPath(s.id))}
                onClick={linkClick(sessionPath(s.id))}
                aria-current={s.id === sessionId ? "page" : undefined}
              >
                {sessionLabel(s.title, s.updated_at)}
              </a>
            </li>
          ))}
        </ul>
        {sessions.length === 0 && <p className="hint">No chats yet.</p>}
      </nav>

      <section className="chat-main" aria-label="Conversation">
        <h1>Chat</h1>
        {!sessionId && (
          <details className="scope" data-testid="scope-picker">
            <summary>{scopeText}</summary>
            <fieldset>
              <legend className="sr-only">Documents to search</legend>
              <label className="check">
                <input type="radio" name="scope" checked={scope.length === 0} onChange={() => setScope([])} />
                All documents
              </label>
              <label className="check">
                <input type="radio" name="scope" checked={scope.length > 0} onChange={() => docs[0] && setScope([docs[0].id])} />
                Selected documents
              </label>
              {scope.length > 0 &&
                docs.map((d) => (
                  <label className="check check-doc" key={d.id}>
                    <input
                      type="checkbox"
                      checked={scope.includes(d.id)}
                      onChange={(e) =>
                        setScope((s) => (e.target.checked ? [...s, d.id] : s.filter((x) => x !== d.id)))
                      }
                    />
                    {d.title}
                  </label>
                ))}
            </fieldset>
          </details>
        )}
        {sessionId && <p className="hint">{scopeText}</p>}
        {loadError && (
          <p className="notice notice-error" role="alert">
            {loadError}
          </p>
        )}
        {!sessionId && messages.length === 0 && !busy && (
          <p className="hint">Ask a question about your documents. Every claim links to the exact passage it came from.</p>
        )}
        <ol className="msgs" ref={logRef} aria-label="Messages" data-testid="messages">
          {messages.map((m, i) => (
            <MessageView
              key={m.id}
              msg={m}
              docCount={scopeCount}
              onOpen={setSource}
              onAsk={(t) => void ask(t)}
              onWhy={setWhy}
              {...(m.role === "assistant" && i > 0 ? { onReask: () => void ask(messages[i - 1]!.content) } : {})}
            />
          ))}
        </ol>
        {failure && (
          <p className="notice notice-error" role="alert" data-testid="chat-error">
            {failure}
          </p>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void ask(input);
          }}
        >
          <label className="field">
            Your question
            <textarea
              value={input}
              rows={2}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
            />
          </label>
          <button className="btn btn-primary" disabled={busy || !input.trim()}>
            {busy ? "Answering…" : "Ask"}
          </button>
        </form>
      </section>

      {(source || why) && (
        <div className="chat-side">
          {source && (
            <SourceViewer
              chunkId={source.chunkId}
              sentenceStart={source.sentenceStart}
              sentenceEnd={source.sentenceEnd}
              onClose={() => setSource(null)}
            />
          )}
          {why && <WhyPanel runId={why} onClose={() => setWhy(null)} />}
        </div>
      )}
    </div>
  );
}
