import type { Route } from "./logic";
import { hrefFor, linkClick } from "./nav";

/** TODO(S19.05): replaced by the chat screen. It only proves the `?q=` and `?doc=` hand-off from the document page. */
export function ChatStub({ route }: { route: Route }) {
  const q = route.params.get("q");
  const doc = route.params.get("doc");
  return (
    <section data-testid="chat-stub">
      <h1>Chat</h1>
      <p className="notice">TODO(S19.05): the chat screen is not built yet. This stub shows the parameters it will receive.</p>
      <dl className="source-meta">
        <div>
          <dt>Question</dt>
          <dd data-testid="chat-q">{q ?? "none"}</dd>
        </div>
        <div>
          <dt>Document</dt>
          <dd className="mono" data-testid="chat-doc">
            {doc ?? "all documents"}
          </dd>
        </div>
      </dl>
      {doc && (
        <p>
          <a href={hrefFor(`/documents/${doc}`)} onClick={linkClick(`/documents/${doc}`)}>
            Back to the document
          </a>
        </p>
      )}
    </section>
  );
}
