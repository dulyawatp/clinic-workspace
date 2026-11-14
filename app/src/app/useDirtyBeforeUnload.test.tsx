import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useDirtyBeforeUnload } from "./useDirtyBeforeUnload";

function dispatchUnload(): Event {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event;
}

describe("useDirtyBeforeUnload", () => {
  it("does not warn for a clean draft", () => {
    renderHook(() => useDirtyBeforeUnload(false));
    expect(dispatchUnload().defaultPrevented).toBe(false);
  });

  it("warns before unload while the draft is dirty", () => {
    renderHook(() => useDirtyBeforeUnload(true));
    expect(dispatchUnload().defaultPrevented).toBe(true);
  });

  it("removes the listener when the draft becomes clean", () => {
    const { rerender } = renderHook(
      ({ dirty }: { dirty: boolean }) => useDirtyBeforeUnload(dirty),
      { initialProps: { dirty: true } },
    );
    rerender({ dirty: false });
    expect(dispatchUnload().defaultPrevented).toBe(false);
  });

  it("removes the listener on unmount", () => {
    const { unmount } = renderHook(() => useDirtyBeforeUnload(true));
    unmount();
    expect(dispatchUnload().defaultPrevented).toBe(false);
  });
});
