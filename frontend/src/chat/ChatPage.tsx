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
  dropLastTurn,
  fromHistory,
  lastServerAnswer,
  lastUser,
  markStopped,
  pendingAssistant,
  reduceTurn,
  sessionLabel,
  sessionPath,
  suggestionsFor,
  userMsg,
  type Msg,
} from "./logic";
import { useFollowScroll } from "./follow";
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
  const pickScope = route.params.get("pick") === "1";
  const draft = route.params.get("draft"); // a question carried over from "Wrong document?"

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
  const [editing, setEditing] = useState(false); // the composer holds the last question being edited
  const [docsLoaded, setDocsLoaded] = useState(false);

  const [activeScope, setActiveScope] = useState<string[] | null>(null);
  const abort = useRef<AbortController | null>(null);
  const pendingId = useRef<{ key: string; id: string } | null>(null); // client_message_id kept for a retry of the same turn
  const live = useRef<string | null>(null); // session id whose messages are local (just created or streaming)
  const autoStarted = useRef<string | null>(null);
  const messagesRef = useRef<Msg[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  messagesRef.current = messages;
  const { showJump, jump, resume } = useFollowScroll(messages);

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
      .catch(unauth)
      .finally(() => setDocsLoaded(true));
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

  useEffect(() => () => abort.current?.abort(), []);

  // "Wrong document?" opens a fresh chat with the scope picker and the question waiting in the composer.
  useEffect(() => {
    if (!sessionId && draft) setInput(draft);
  }, [sessionId, draft]);

  /** One turn: a typed question, a re-ask of an earlier message, or an option picked from a clarification
   * (`question` is only what the pair shows; the server resolves the scope from the option). */
  const send = useCallback(
    async (
      turn: (
        | { content: string }
        | { reaskOf: string; question: string }
        | { clarify: { messageId: string; option: number }; question: string }
      ) & { replaces?: string },
      sid: string,
      replaceLast = false,
    ) => {
      const typed = "content" in turn;
      const reaskOf = "reaskOf" in turn ? turn.reaskOf : null;
      const clarify = "clarify" in turn ? turn.clarify : null;
      const text = (typed ? turn.content : turn.question).trim();
      if (!text) return;
      setFailure(null);
      setBusy(true);
      resume();
      const before = messagesRef.current;
      const key = reaskOf ? `reask:${reaskOf}` : clarify ? `clarify:${clarify.messageId}:${clarify.option}` : `ask:${text}`;
      // A retry of the same turn reuses its client_message_id; a different turn gets a new one.
      const clientId = pendingId.current?.key === key ? pendingId.current.id : crypto.randomUUID();
      pendingId.current = { key, id: clientId };
      const userId = `u-${clientId}`;
      const tmp = `a-${clientId}`;
      setMessages((m) => [...(replaceLast ? dropLastTurn(m) : m).filter((x) => x.id !== userId && x.id !== tmp), userMsg(userId, text), pendingAssistant(tmp)]);
      const ctl = new AbortController();
      abort.current = ctl;
      let ended = false;
      let streamError: string | null = null;
      try {
        for await (const e of chatApi.send(sid, { ...(typed ? { content: text } : reaskOf ? { reaskOf } : { clarifyChoice: clarify! }), ...(turn.replaces ? { replaces: turn.replaces } : {}) }, clientId, ctl.signal)) {
          if (e.type === "done") ended = true;
          if (e.type === "error") streamError = e.code;
          setMessages((m) => m.map((x) => (x.id === tmp || (x.streaming && x.role === "assistant") ? reduceTurn(x, e) : x)));
        }
        if (!ended && !streamError) throw new NetworkError("Network error: the stream ended early.");
        if (streamError && !ended) {
          setFailure(describeFailure({ stream: streamError }).message);
          if (typed) setInput(text);
          return;
        }
        pendingId.current = null;
        if (typed) setInput("");
        void refreshSessions();
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          if (typed) setInput(""); // stopped: the partial answer stays, so the question is not pending any more
          return;
        }
        unauth(e);
        const f =
          e instanceof ApiError
            ? describeFailure({ http: e.status, code: e.code, retryAfterS: e.retryAfterS })
            : describeFailure({ network: true });
        // Pre-stream failures never produced an answer: restore the log as it was and keep the text.
        setMessages(before);
        setFailure(f.message);
        if (typed) setInput(text);
        if (!f.reuseId) pendingId.current = null;
      } finally {
        setBusy(false);
      }
    },
    [refreshSessions, unauth, resume],
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
      const replace = editing;
      setEditing(false);
      const old = replace ? lastServerAnswer(messagesRef.current) : null;
      await send({ content: text, ...(old ? { replaces: old } : {}) }, sid, replace);
    },
    [busy, sessionId, scope, startSession, send, editing],
  );

  const stop = useCallback(() => {
    abort.current?.abort();
    pendingId.current = null; // a stopped turn is finished: the next ask is a new message
    setMessages((m) => m.map(markStopped));
  }, []);

  const regenerate = useCallback(async () => {
    const q = lastUser(messagesRef.current);
    if (busy || !sessionId || !q) return;
    pendingId.current = null; // a new client_message_id, same scope (the session's)
    const old = lastServerAnswer(messagesRef.current);
    // The server re-asks the stored question and hides the replaced pair, so a reload shows one answer.
    await send(old ? { reaskOf: old, question: q.content, replaces: old } : { content: q.content }, sessionId, true);
  }, [busy, sessionId, send]);

  const editLast = useCallback(() => {
    const q = lastUser(messagesRef.current);
    if (busy || !sessionId || !q) return;
    setInput(q.content);
    setEditing(true);
    inputRef.current?.focus();
  }, [busy, sessionId]);

  // Esc stops a running answer from anywhere on the page.
  useEffect(() => {
    if (!busy) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) stop();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, stop]);

  const reask = useCallback(
    async (messageId: string) => {
      if (busy || !sessionId) return;
      const i = messages.findIndex((m) => m.id === messageId);
      const question = messages[i]?.role === "user" ? messages[i]!.content : (messages[i - 1]?.content ?? "");
      await send({ reaskOf: messageId, question: question || "Re-asked on the latest version" }, sessionId);
    },
    [busy, sessionId, messages, send],
  );

  /** A chip of a clarification: ask the clarified question again, scoped by the server to the chosen document. */
  const pickOption = useCallback(
    async (messageId: string, option: number) => {
      if (busy || !sessionId) return;
      const i = messagesRef.current.findIndex((m) => m.id === messageId);
      const question = messagesRef.current.slice(0, Math.max(i, 0)).reverse().find((m) => m.role === "user")?.content ?? "";
      await send({ clarify: { messageId, option }, question: question || "Your question" }, sessionId);
    },
    [busy, sessionId, send],
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
          <details className="scope" data-testid="scope-picker" open={pickScope || undefined}>
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
          <div className="chat-empty" data-testid="chat-empty">
            <p className="empty-lead">Ask anything about your documents. Every claim points back to the passage it came from.</p>
            {docsLoaded && docs.length === 0 ? (
              <p className="hint">Nothing here yet. Add a document in your library, then come back and ask.</p>
            ) : (
              docsLoaded && (
                <ul className="qchips" aria-label="Suggested questions" data-testid="suggestions">
                  {suggestionsFor(docs, scope).map((t) => (
                    <li key={t}>
                      <button type="button" className="qchip" onClick={() => void ask(t)}>
                        {t}
                      </button>
                    </li>
                  ))}
                </ul>
              )
            )}
          </div>
        )}
        <ol className="msgs" aria-label="Messages" data-testid="messages">
          {messages.map((m, i) => {
            const last = i === messages.length - 1;
            const lastUserId = lastUser(messages)?.id;
            const idle = !busy && !!sessionId;
            const question = m.role === "assistant" ? messages.slice(0, i).reverse().find((x) => x.role === "user")?.content : undefined;
            return (
              <MessageView
                key={m.id}
                msg={m}
                {...(question ? { question } : {})}
                docCount={scopeCount}
                busy={busy}
                isLast={last}
                onOpen={setSource}
                onAsk={(t) => void ask(t)}
                onWhy={setWhy}
                onPick={(option) => void pickOption(m.id, option)}
                {...(m.role === "assistant" && idle ? { onReask: (id: string) => void reask(id) } : {})}
                {...(m.role === "assistant" && idle && last && !m.streaming ? { onRegenerate: () => void regenerate() } : {})}
                {...(m.role === "user" && idle && m.id === lastUserId ? { onEdit: editLast } : {})}
              />
            );
          })}
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
          {showJump && (
            <button type="button" className="btn jump" onClick={jump} data-testid="jump-latest">
              Jump to latest
            </button>
          )}
          <label className="field">
            {editing ? "Edit your question" : "Your question"}
            <textarea
              ref={inputRef}
              value={input}
              rows={2}
              aria-describedby="composer-hint"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                } else if (e.key === "Escape" && editing && !busy) {
                  setEditing(false);
                  setInput("");
                } else if (e.key === "ArrowUp" && input === "" && !busy) {
                  e.preventDefault();
                  editLast();
                }
              }}
            />
          </label>
          <div className="composer-row">
            {busy ? (
              <button type="button" className="btn" onClick={stop} data-testid="stop">
                Stop
              </button>
            ) : (
              <button className="btn btn-primary" disabled={!input.trim()}>
                {editing ? "Resend" : "Ask"}
              </button>
            )}
            <p className="hint" id="composer-hint">
              {busy
                ? "Esc stops the answer."
                : editing
                  ? "Resending replaces your last question and its answer. Esc cancels."
                  : "Enter sends, Shift+Enter adds a line, and the up arrow edits your last question."}
            </p>
          </div>
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
