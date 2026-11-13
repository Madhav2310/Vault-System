import { useCallback, useState } from "react";
import { api, ApiError, getApiKey, setApiKey } from "./api/client";
import { ChatStub } from "./document/ChatStub";
import { DocumentPage } from "./document/DocumentPage";
import { useRoute } from "./document/nav";
import { Library } from "./library/Library";

/** Demo auth assumption (S19): the API key is typed here and kept in localStorage. */
function KeyScreen({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const previous = getApiKey();
    setApiKey(value.trim());
    try {
      await api.ping();
      onSaved();
    } catch (err) {
      setApiKey(previous);
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
      <p className="hint">Stored in this browser only. It is sent as a bearer token to this server.</p>
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
  const [hasKey, setHasKey] = useState(() => getApiKey() !== "");
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
            <button
              className="btn btn-quiet"
              onClick={() => {
                setApiKey("");
                setHasKey(false);
              }}
            >
              Change API key
            </button>
          )}
        </header>
        <main id="main">
          {!hasKey ? (
            <KeyScreen onSaved={() => setHasKey(true)} />
          ) : route.name === "document" && route.id ? (
            <DocumentPage id={route.id} onUnauthenticated={onUnauth} />
          ) : route.name === "chat" ? (
            <ChatStub route={route} />
          ) : (
            <Library onUnauthenticated={onUnauth} />
          )}
        </main>
      </div>
    </>
  );
}
