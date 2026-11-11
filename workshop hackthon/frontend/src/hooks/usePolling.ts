import { useEffect, useRef } from "react";

/**
 * Calls ``callback`` every ``intervalMs`` while ``active`` is true (first call immediately).
 * Used for pipeline status: the backend reports the real current stage from its run tracker.
 */
export function usePolling(callback: () => Promise<boolean | void> | boolean | void, intervalMs: number, active: boolean): void {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      const stop = await saved.current();
      if (!cancelled && stop !== true) timer = window.setTimeout(tick, intervalMs);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, intervalMs]);
}
