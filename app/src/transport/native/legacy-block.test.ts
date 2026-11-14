import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readLivePhotos } from "../../features/photos/live-photos";
import { server } from "../../test/msw/server";
import { fetchCsrf } from "../auth";
import { mapHttpError } from "../error-map";
import { photoStatus, savePhoto } from "../photo-save";
import { readQuery } from "../read/client";

const UNSUPPORTED = { code: "UNSUPPORTED", retryable: false };

/** Every `/api/*` call that is not the native Frappe surface. */
function legacyCalls(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls
    .map((call) => String(call[0]))
    .filter(
      (url) => url.startsWith("/api/") && !url.startsWith("/api/method/"),
    );
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("native mode refuses the legacy proxy API", () => {
  it("refuses generic reads without opening a request", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(readQuery("patients")).rejects.toEqual(UNSUPPORTED);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses the proxy CSRF handshake", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(fetchCsrf()).rejects.toEqual(UNSUPPORTED);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses photo writes and photo status reads", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(savePhoto("ws-1", "key-1", "aW1n")).rejects.toEqual(
      UNSUPPORTED,
    );
    await expect(photoStatus("op-1")).rejects.toEqual(UNSUPPORTED);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses live photo list reads", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(
      readLivePhotos("ws-1", "native-1", 0, new AbortController().signal),
    ).rejects.toEqual(UNSUPPORTED);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("proxy mode keeps the legacy transport", () => {
  it("still reads through the proxy route", async () => {
    server.use(
      http.get("*/api/read/patients", () =>
        HttpResponse.json({ items: [{ id: "row" }], total: 1 }),
      ),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(readQuery("patients")).resolves.toEqual({
      items: [{ id: "row" }],
      total: 1,
    });
    expect(legacyCalls(fetchSpy)).toEqual(["/api/read/patients"]);
  });

  it("still maps proxy failures to safe error codes", () => {
    expect(mapHttpError(403, { message: "private trace" })).toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
  });
});
