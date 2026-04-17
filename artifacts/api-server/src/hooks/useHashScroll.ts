import { useEffect, useRef } from "react";

/**
 * Scrolls once to the element whose id matches the current URL hash,
 * then removes the hash so the scroll doesn't re-trigger on re-renders.
 *
 * @param knownHashes - allowlist of hashes this page handles (e.g. ["#my-section"])
 * @param deps        - extra dependencies that gate when the DOM is ready (e.g. loading flags)
 */
export function useHashScroll(knownHashes: string[], deps: unknown[] = []) {
  const handledRef = useRef<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash;
    if (!hash || handledRef.current === hash) return;
    if (!knownHashes.includes(hash)) return;

    const el = document.getElementById(hash.slice(1));
    if (!el) return;

    handledRef.current = hash;
    el.scrollIntoView({ behavior: "smooth", block: "start" });

    const timerId = setTimeout(() => {
      history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
    }, 1500);

    return () => clearTimeout(timerId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
