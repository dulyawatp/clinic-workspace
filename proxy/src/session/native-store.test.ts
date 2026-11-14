import { describe, expect, it, vi } from "vitest";
import { NativeCredentialStore } from "./native-store.js";

const SESSION = "session-1";
const FUTURE = 2_000;
const NOW = 1_000;

describe("NativeCredentialStore", () => {
  it("keeps the credential in process memory and returns a copy", () => {
    const store = new NativeCredentialStore(() => NOW);
    store.set(SESSION, { sid: "sid", csrf: "csrf" }, FUTURE);

    const first = store.get(SESSION);
    expect(first).toEqual({ sid: "sid", csrf: "csrf" });
    if (first !== undefined) first.sid = "tampered";
    expect(store.get(SESSION)).toEqual({ sid: "sid", csrf: "csrf" });
  });

  it("expires with the session and drops the credential", () => {
    vi.useFakeTimers();
    try {
      const store = new NativeCredentialStore();
      store.set(SESSION, { sid: "sid", csrf: "csrf" }, Date.now() + 10);
      vi.setSystemTime(Date.now() + 11);
      expect(store.get(SESSION)).toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("clears on logout and refuses malformed credentials", () => {
    const store = new NativeCredentialStore(() => NOW);
    store.set(SESSION, { sid: "sid", csrf: "csrf" }, FUTURE);
    store.clear(SESSION);
    expect(store.get(SESSION)).toBeUndefined();

    expect(() =>
      store.set(SESSION, { sid: "bad;sid", csrf: "csrf" }, FUTURE),
    ).toThrow("INVALID_NATIVE_CREDENTIALS");
  });
});
