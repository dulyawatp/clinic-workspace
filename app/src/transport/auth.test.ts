import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { sanitizeReturnTo } from "../app/return-to";
import { SessionProvider, useSession } from "../app/SessionProvider";
import { attemptLogin } from "../test/auth";
import { createHandlers, FIXTURE_CSRF_TOKEN } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { CSRF_HEADER, fetchCsrf, postLogin, postLogout } from "./auth";

function SessionWrapper({ children }: { children: ReactNode }) {
  return createElement(SessionProvider, { children });
}

describe("authentication transport", () => {
  it("fetches CSRF with same-origin credentials and no HTTP caching", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      expect(await fetchCsrf()).toEqual({ token: FIXTURE_CSRF_TOKEN });
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/auth/csrf",
        expect.objectContaining({
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
        }),
      );
      expect(CSRF_HEADER).toBe("x-clinic-csrf");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("fetches a fresh token for each POST and sends no logout body", async () => {
    let tokenNumber = 0;
    const received: Request[] = [];
    server.use(
      http.get("/api/auth/csrf", () =>
        HttpResponse.json({ token: `token-${++tokenNumber}` }),
      ),
      http.post("/api/auth/login", async ({ request }) => {
        received.push(request);
        expect(request.headers.get(CSRF_HEADER)).toBe(`token-${tokenNumber}`);
        expect(await request.clone().json()).toEqual({
          usr: "rec",
          pwd: "fixture",
        });
        return HttpResponse.json({
          sessionEpoch: "test-session",
          user: { id: "u1", displayName: "Fixture", roles: ["REC"] },
          capabilities: {},
        });
      }),
      http.post("/api/auth/logout", ({ request }) => {
        received.push(request);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await postLogin("rec", "fixture");
      await postLogout();
      for (const path of ["/api/auth/login", "/api/auth/logout"]) {
        expect(fetchSpy).toHaveBeenCalledWith(
          path,
          expect.objectContaining({
            method: "POST",
            cache: "no-store",
            credentials: "same-origin",
          }),
        );
      }
    } finally {
      fetchSpy.mockRestore();
    }
    expect(tokenNumber).toBe(2);
    expect(received[1]?.headers.get(CSRF_HEADER)).toBe("token-2");
    for (const request of received) {
      expect(request.credentials).toBe("same-origin");
      expect(request.headers.get("content-type")).toBe("application/json");
    }
    expect(await received[1]?.text()).toBe("");
  });

  it("requires CSRF on logout and becomes anonymous after caller refresh", async () => {
    server.use(...createHandlers({ role: "REC" }));
    const { result } = renderHook(() => useSession(), {
      wrapper: SessionWrapper,
    });
    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    const rejected = await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
    });
    expect(rejected.status).toBe(403);
    await postLogout();
    await act(() => result.current.refresh());
    expect(result.current.status).toBe("anonymous");
  });

  it("never persists password, token or returned session", async () => {
    server.use(...createHandlers({ authed: false }));
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(Document.prototype, "cookie", "set");
    try {
      expect((await attemptLogin()).user.roles).toEqual(["REC"]);
      expect(storage).not.toHaveBeenCalled();
      expect(cookie).not.toHaveBeenCalled();
    } finally {
      storage.mockRestore();
      cookie.mockRestore();
    }
  });

  it("maps rejected login to safe code without upstream content", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { code: "FORBIDDEN", message: "private trace", stack: "secret" },
          { status: 403 },
        ),
      ),
    );
    await expect(postLogin("rec", "nope")).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
  });

  it("maps network failures without exposing their text", async () => {
    server.use(http.get("/api/auth/csrf", () => HttpResponse.error()));
    await expect(postLogin("rec", "fixture")).rejects.toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
  });

  it.each([{}, { token: "" }, { token: "   " }, { token: 7 }])(
    "rejects malformed CSRF token %j before any login POST",
    async (body) => {
      const posted = vi.fn();
      server.use(
        http.get("/api/auth/csrf", () => HttpResponse.json(body)),
        http.post("/api/auth/login", () => {
          posted();
          return HttpResponse.json({});
        }),
      );
      await expect(postLogin("rec", "fixture")).rejects.toMatchObject({
        code: "UPSTREAM_UNAVAILABLE",
      });
      expect(posted).not.toHaveBeenCalled();
    },
  );

  it("does not POST after denied CSRF handshake", async () => {
    const posted = vi.fn();
    server.use(
      http.get("/api/auth/csrf", () =>
        HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
      ),
      http.post("/api/auth/logout", () => {
        posted();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await expect(postLogout()).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(posted).not.toHaveBeenCalled();
  });

  it("rejects malformed successful session rather than inventing identity", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ user: { roles: ["UNKNOWN"] } }),
      ),
    );
    await expect(postLogin("rec", "fixture")).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
  });
});

describe("sanitizeReturnTo", () => {
  it("retains only allowlisted query keys and drops hash/private query", () => {
    expect(
      sanitizeReturnTo(
        "/clinic/profile?status=open&view=day&section=main&page=2&range=week&patientName=Private#private-note",
      ),
    ).toBe(
      "/clinic/profile?status=open&view=day&section=main&page=2&range=week",
    );
  });

  it.each([
    null,
    "",
    "/other",
    "//evil.test/clinic/",
    "/clinic//evil.test",
    "https://evil.test/clinic/",
    "/clinic/../../other",
    "/clinic/%2f%2fevil.test",
    "/clinic/%5cpath",
    "/clinic/%252fpath",
    "/clinic/\\evil.test",
    "/clinic/\nprofile",
  ])("rejects unsafe or non-clinic target %j", (raw) => {
    expect(sanitizeReturnTo(raw)).toBeNull();
  });
});
