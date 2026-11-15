import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api/client";
import { ChatPage } from "./chat/ChatPage";
import { DocumentPage } from "./document/DocumentPage";
import { hrefFor, linkClick, useRoute } from "./document/nav";
import { Library } from "./library/Library";
import { MetricsPage } from "./metrics/MetricsPage";

/** The API key is typed once and exchanged for an httpOnly session cookie; the page never stores it. */
function KeyScreen({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(value.trim());
      setValue("");
      onSaved();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That key was not accepted."
          : "Could not reach the server. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} style={{ maxWidth: 420 }}>
      <h1>Enter your API key</h1>
      <p className="hint">Exchanged for a secure session cookie; this page does not keep the key.</p>
      <label className="field">
        API key
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          required
        />
      </label>
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
      <p>
        <button className="btn btn-primary" disabled={busy || !value.trim()}>
          {busy ? "Checking…" : "Continue"}
        </button>
      </p>
    </form>
  );
}

export function App() {
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  // Only a key typed on this page makes "Change API key" the right words; a cookie or local sign-in signs out.
  const [keyEntered, setKeyEntered] = useState(false);
  useEffect(() => {
    let live = true;
    // A valid cookie wins; otherwise a local run signs in on its own; only then is a key asked for.
    api
      .hasSession()
      .then((ok) => (ok ? true : api.loginLocal().catch(() => false)))
      .then((ok) => live && setHasKey(ok))
      .catch(() => live && setHasKey(false));
    return () => {
      live = false;
    };
  }, []);
  const route = useRoute();
  const onUnauth = useCallback(() => setHasKey(false), []);

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <div className="shell">
        <header className="topbar">
          <span className="brand">Vault</span>
          {hasKey && (
            <nav className="topnav" aria-label="Main">
              <a
                href={hrefFor("/")}
                onClick={linkClick("/")}
                aria-current={route.name === "library" || route.name === "document" ? "page" : undefined}
              >
                Library
              </a>
              <a
                href={hrefFor("/chat")}
                onClick={linkClick("/chat")}
                aria-current={route.name === "chat" ? "page" : undefined}
              >
                Chat
              </a>
              <a
                href={hrefFor("/metrics")}
                onClick={linkClick("/metrics")}
                aria-current={route.name === "metrics" ? "page" : undefined}
              >
                Metrics
              </a>
            </nav>
          )}
          {hasKey && (
            <button
              className="btn btn-quiet"
              onClick={() => {
                void api.logout().catch(() => undefined);
                setHasKey(false);
                setKeyEntered(false);
              }}
            >
              {keyEntered ? "Change API key" : "Sign out"}
            </button>
          )}
        </header>
        <main id="main">
          {hasKey === null ? null : !hasKey ? (
            <KeyScreen
              onSaved={() => {
                setKeyEntered(true);
                setHasKey(true);
              }}
            />
          ) : route.name === "document" && route.id ? (
            <DocumentPage id={route.id} onUnauthenticated={onUnauth} />
          ) : route.name === "chat" ? (
            <ChatPage route={route} onUnauthenticated={onUnauth} />
          ) : route.name === "metrics" ? (
            <MetricsPage onUnauthenticated={onUnauth} />
          ) : (
            <Library onUnauthenticated={onUnauth} />
          )}
        </main>
      </div>
    </>
  );
}
