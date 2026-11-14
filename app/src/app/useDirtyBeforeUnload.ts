import { useEffect } from "react";

/**
 * Warns before a full page unload only while `dirty`. The in-app navigation
 * guard (`useBlocker`) is a separate concern; this hook covers browser
 * reload/close for the same draft state.
 */
export function useDirtyBeforeUnload(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
}
