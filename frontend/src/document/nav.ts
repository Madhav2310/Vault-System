import { useEffect, useState } from "react";
import { parseRoute, type Route } from "./logic";

const BASE = import.meta.env.BASE_URL; // "/app/"

/** Client-side navigation without a router dependency. `path` is app-relative: "/documents/ID", "/chat?q=…". */
export function navigate(path: string): void {
  history.pushState(null, "", BASE + path.replace(/^\//, ""));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function hrefFor(path: string): string {
  return BASE + path.replace(/^\//, "");
}

const current = (): Route => parseRoute(location.pathname, location.search, BASE);

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(current);
  useEffect(() => {
    const on = () => setRoute(current());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return route;
}

/** Anchor click handler: plain left-clicks navigate in-app, modified clicks keep browser behaviour. */
export function linkClick(path: string) {
  return (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(path);
  };
}
