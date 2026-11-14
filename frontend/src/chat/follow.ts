import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { isNearBottom } from "./logic";

const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const atBottom = () => isNearBottom(window.scrollY, window.innerHeight, document.documentElement.scrollHeight);
const toEnd = (smooth: boolean) =>
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth && !reduced() ? "smooth" : "auto" });

/**
 * Keep the newest content in view while it streams, but stop the moment the reader scrolls up.
 * `dep` changes whenever content may have grown. `showJump` is true when the reader is away from the end.
 */
export function useFollowScroll(dep: unknown) {
  const following = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      following.current = atBottom();
      setShowJump(!following.current);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    if (following.current) toEnd(false);
    else setShowJump(!atBottom());
  }, [dep]);

  const resume = useCallback(() => {
    following.current = true;
    setShowJump(false);
  }, []);
  const jump = useCallback(() => {
    resume();
    toEnd(true);
  }, [resume]);
  return { showJump, jump, resume };
}
