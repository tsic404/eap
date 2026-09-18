import { useEffect, useState } from "react";

/**
 * Tracks a CSS media query via `matchMedia`. Reports `false` on the server and
 * in environments without `matchMedia` (e.g. jsdom), then settles after mount.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const onChange = (event: MediaQueryListEvent) => {
      setMatches(event.matches);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
