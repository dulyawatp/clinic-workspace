import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CAPABILITY_NAMES } from "../../domain/capability";
import { server } from "../../test/msw/server";
import { fetchNativeDirectoryBody } from "./directory";
import { nativeTransportEnabled } from "./flag";
import {
  classifyNativeForbidden,
  clearNativeCsrfToken,
  fetchNativeSession,
  nativeCsrfToken,
  nativeLogin,
  nativeLogout,
} from "./session";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const DIRECTORY = "/api/method/clinic_ui.api.directory.synthetic_patients";
const LOGIN = "/api/method/login";
const LOGOUT = "/api/method/logout";
const CSRF_HEADER = "X-Frappe-CSRF-Token";
const TOKEN = "native-csrf-token";

function liveMessage(overrides: Record<string, unknown> = {}) {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["DOC"] },
    capabilities: {
      "read.patient": "enabled",
      "write.patient.create": "enabled",
      "write.encounter.draft": "enabled",
    },
    csrfToken: TOKEN,
    ...overrides,
  };
}

function serveBootstrap(message: unknown, status = 200) {
  server.use(
    http.get(BOOTSTRAP, () => HttpResponse.json({ message }, { status })),
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  clearNativeCsrfToken();
});

// Frozen by the full write-contract design. Kept literal so a contract edit
// without a client union update fails here, not at runtime.
const FROZEN_CAPABILITY_NAMES = [
  "write.patient.create",
  "write.patient.update",
  "write.appointment.book",
  "write.appointment.reschedule",
  "write.appointment.cancel",
  "write.appointment.no_show",
  "write.appointment.checkin",
  "write.encounter.draft",
  "write.encounter.complete",
  "write.encounter.amend",
  "write.medication.order",
  "write.procedure.complete",
  "write.lab.order",
  "write.lab.review",
  "write.photo.consent",
  "write.photo.upload",
  "write.photo.link",
  "write.photo.annotate",
  "write.stock.receive",
  "write.stock.transfer",
  "write.stock.adjust",
  "write.stock.consume",
  "write.purchase_order.draft",
  "write.purchase_order.submit",
  "write.invoice.submit",
  "write.invoice.void_amend",
  "write.payment.receive",
  "write.payment.refund",
  "write.admin.user",
  "write.admin.master",
  "read.patient",
  "read.appointment",
  "read.encounter",
  "read.lab",
  "read.photo",
  "read.inventory",
  "read.billing",
  "read.report",
  "read.admin",
];

describe("native capability contract", () => {
  it("keeps the contract JSON, closed union and order in lockstep", () => {
    expect(CAPABILITY_NAMES).toEqual(FROZEN_CAPABILITY_NAMES);
  });
});

describe("native transport opt-in", () => {
  it("stays disabled unless the build sets VITE_CLINIC_NATIVE=1", () => {
    expect(nativeTransportEnabled()).toBe(false);
    for (const value of ["", "0", "true", "yes", " 1", "1 ", "01"]) {
      vi.stubEnv("VITE_CLINIC_NATIVE", value);
      expect(nativeTransportEnabled()).toBe(false);
    }
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    expect(nativeTransportEnabled()).toBe(true);
  });
});

describe("native session bootstrap", () => {
  it("reads the Frappe method and unwraps only the message envelope", async () => {
    serveBootstrap(liveMessage());
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const session = await fetchNativeSession();

    expect(session).toEqual({
      mode: "live",
      sessionEpoch: "native-epoch-1",
      user: { id: "native|u1", displayName: "Native User", roles: ["DOC"] },
      capabilities: {
        "read.patient": "read_only",
        "write.patient.create": "enabled",
        "write.encounter.draft": "enabled",
      },
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      BOOTSTRAP,
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
  });

  it("enables only explicitly allowed patient writes and fails closed for other values", async () => {
    serveBootstrap(liveMessage({ capabilities: {
      "write.patient.create": "enabled",
      "write.patient.update": "enabled",
      "write.encounter.draft": "enabled",
      "write.unknown": "enabled",
      "read.patient": "enabled",
    } }));
    expect((await fetchNativeSession())?.capabilities).toEqual({
      "write.patient.create": "enabled",
      "write.patient.update": "enabled",
      "write.encounter.draft": "enabled",
      "read.patient": "read_only",
    });
    for (const value of [null, true, {}, "ENABLED", "read_only", "denied", ""]) {
      serveBootstrap(liveMessage({ capabilities: { "write.patient.create": value, "write.patient.update": value } }));
      expect((await fetchNativeSession())?.capabilities).toEqual({
        "write.patient.create": "read_only",
        "write.patient.update": "read_only",
      });
    }
  });

  it("enables the five appointment writes only on an exact enabled state", async () => {
    serveBootstrap(
      liveMessage({
        capabilities: {
          "write.appointment.book": "enabled",
          "write.appointment.reschedule": "enabled",
          "write.appointment.cancel": "enabled",
          "write.appointment.no_show": "enabled",
          "write.appointment.checkin": "enabled",
          "write.admin.master": "enabled",
        },
      }),
    );
    expect((await fetchNativeSession())?.capabilities).toEqual({
      "write.appointment.book": "enabled",
      "write.appointment.reschedule": "enabled",
      "write.appointment.cancel": "enabled",
      "write.appointment.no_show": "enabled",
      "write.appointment.checkin": "enabled",
      // Not enableable by deployment yet: it has no reviewed write family.
      "write.admin.master": "read_only",
    });

    for (const value of [null, true, {}, "ENABLED", "enabled ", "denied"]) {
      serveBootstrap(
        liveMessage({
          capabilities: {
            "write.appointment.book": value,
            "write.appointment.reschedule": value,
            "write.appointment.cancel": value,
            "write.appointment.no_show": value,
            "write.appointment.checkin": value,
          },
        }),
      );
      expect((await fetchNativeSession())?.capabilities).toEqual({
        "write.appointment.book": "read_only",
        "write.appointment.reschedule": "read_only",
        "write.appointment.cancel": "read_only",
        "write.appointment.no_show": "read_only",
        "write.appointment.checkin": "read_only",
      });
    }
  });

  it("keeps the CSRF token in memory only", async () => {
    serveBootstrap(liveMessage());
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(Document.prototype, "cookie", "set");

    await fetchNativeSession();

    expect(nativeCsrfToken()).toBe(TOKEN);
    expect(storage).not.toHaveBeenCalled();
    expect(cookie).not.toHaveBeenCalled();
  });

  it.each([
    ["missing", undefined],
    ["blank", "   "],
    ["non-string", 7],
    ["oversized", "a".repeat(257)],
    ["control characters", "token\nvalue"],
  ])(
    "rejects a csrf token that is %s instead of caching it",
    async (_label, value) => {
      serveBootstrap(
        liveMessage(
          value === undefined ? { csrfToken: undefined } : { csrfToken: value },
        ),
      );
      await expect(fetchNativeSession()).rejects.toMatchObject({
        code: "UPSTREAM_UNAVAILABLE",
      });
      expect(nativeCsrfToken()).toBeNull();
    },
  );

  it.each([
    ["a non-live mode", liveMessage({ mode: "fixture" })],
    ["a missing mode", liveMessage({ mode: undefined })],
    [
      "an unknown role",
      liveMessage({
        user: { id: "u", displayName: "d", roles: ["System Manager"] },
      }),
    ],
    ["no session epoch", liveMessage({ sessionEpoch: "" })],
    ["no capabilities", liveMessage({ capabilities: undefined })],
  ])("fails closed for %s", async (_label, message) => {
    serveBootstrap(message);
    await expect(fetchNativeSession()).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
    expect(nativeCsrfToken()).toBeNull();
  });

  it("rejects a body without a Frappe message envelope", async () => {
    server.use(http.get(BOOTSTRAP, () => HttpResponse.json({ mode: "live" })));
    await expect(fetchNativeSession()).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
  });

  it("treats 401 as no session and drops the stale token", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    server.use(
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
      ),
    );
    await expect(fetchNativeSession()).resolves.toBeNull();
    expect(nativeCsrfToken()).toBeNull();
  });

  it("does not let a superseded bootstrap overwrite the accepted CSRF token", async () => {
    const pending: Array<(response: Response) => void> = [];
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pending.push(resolve);
          }),
      ),
    );

    const older = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));
    const newer = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(2));

    pending[1]?.(
      HttpResponse.json({
        message: liveMessage({ sessionEpoch: "epoch-b", csrfToken: "token-b" }),
      }),
    );
    await expect(newer).resolves.toMatchObject({ sessionEpoch: "epoch-b" });
    expect(nativeCsrfToken()).toBe("token-b");

    // The delayed older response belongs to a superseded generation/identity.
    pending[0]?.(
      HttpResponse.json({
        message: liveMessage({ sessionEpoch: "epoch-a", csrfToken: "token-a" }),
      }),
    );
    await expect(older).resolves.toMatchObject({ sessionEpoch: "epoch-a" });
    expect(nativeCsrfToken()).toBe("token-b");
  });

  it("does not let a superseded 401 clear the accepted CSRF token", async () => {
    const pending: Array<(response: Response) => void> = [];
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pending.push(resolve);
          }),
      ),
    );

    const older = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));
    const newer = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(2));

    pending[1]?.(
      HttpResponse.json({
        message: liveMessage({ sessionEpoch: "epoch-b", csrfToken: "token-b" }),
      }),
    );
    await expect(newer).resolves.toMatchObject({ sessionEpoch: "epoch-b" });
    expect(nativeCsrfToken()).toBe("token-b");

    pending[0]?.(
      HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
    );
    await expect(older).resolves.toBeNull();
    expect(nativeCsrfToken()).toBe("token-b");
  });

  it("does not confuse 403 with an anonymous session", async () => {
    serveBootstrap({ exception: "frappe.exceptions.PermissionError" }, 403);
    await expect(fetchNativeSession()).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
  });

  it("maps network failure without exposing transport text", async () => {
    server.use(http.get(BOOTSTRAP, () => HttpResponse.error()));
    await expect(fetchNativeSession()).rejects.toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
  });
});

describe("bootstrap credential invalidation", () => {
  it("drops the accepted token when the newest bootstrap is rejected", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    serveBootstrap({ exception: "frappe.exceptions.PermissionError" }, 403);
    await expect(fetchNativeSession()).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
    // The unverified session must not keep the earlier credential.
    expect(nativeCsrfToken()).toBeNull();
  });

  it("drops the accepted token when the newest bootstrap is malformed", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    serveBootstrap(liveMessage({ csrfToken: "   " }));
    await expect(fetchNativeSession()).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
    expect(nativeCsrfToken()).toBeNull();
  });

  it("does not reinstall credentials from a bootstrap pending at invalidation", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    const pending: Array<(response: Response) => void> = [];
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pending.push(resolve);
          }),
      ),
    );
    const inFlight = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));

    // Explicit invalidation, e.g. a CSRF rejection or a completed logout.
    clearNativeCsrfToken();
    pending[0]?.(HttpResponse.json({ message: liveMessage() }));

    await expect(inFlight).resolves.toMatchObject({
      sessionEpoch: "native-epoch-1",
    });
    expect(nativeCsrfToken()).toBeNull();
  });

  it("keeps the accepted token when an older bootstrap fails late", async () => {
    const pending: Array<(response: Response) => void> = [];
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pending.push(resolve);
          }),
      ),
    );

    const older = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));
    const newer = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(2));

    pending[1]?.(
      HttpResponse.json({
        message: liveMessage({ sessionEpoch: "epoch-b", csrfToken: "token-b" }),
      }),
    );
    await expect(newer).resolves.toMatchObject({ sessionEpoch: "epoch-b" });
    expect(nativeCsrfToken()).toBe("token-b");

    // The late failure belongs to a superseded generation.
    pending[0]?.(new HttpResponse(null, { status: 500 }));
    await expect(older).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
    expect(nativeCsrfToken()).toBe("token-b");
  });

  it("keeps credentials cleared by logout even when a bootstrap resolves later", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    const pending: Array<(response: Response) => void> = [];
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pending.push(resolve);
          }),
      ),
      http.post(LOGOUT, () => HttpResponse.json({ message: "Logged Out" })),
    );

    const inFlight = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));

    await nativeLogout();
    expect(nativeCsrfToken()).toBeNull();

    pending[0]?.(
      HttpResponse.json({
        message: liveMessage({ csrfToken: "stale-token" }),
      }),
    );
    await expect(inFlight).resolves.toMatchObject({
      sessionEpoch: "native-epoch-1",
    });
    expect(nativeCsrfToken()).toBeNull();
  });
});

describe("native forbidden classification", () => {
  it("classifies without replacing the accepted token", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    server.use(
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({
          message: liveMessage({
            sessionEpoch: "probe-epoch",
            csrfToken: "probe-token",
          }),
        }),
      ),
    );

    await expect(
      classifyNativeForbidden(new AbortController().signal),
    ).resolves.toEqual({ code: "FORBIDDEN", retryable: false });

    expect(nativeCsrfToken()).toBe(TOKEN);
  });

  it("leaves credential invalidation to the provider when no session remains", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    expect(nativeCsrfToken()).toBe(TOKEN);

    server.use(
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
      ),
    );

    await expect(
      classifyNativeForbidden(new AbortController().signal),
    ).resolves.toEqual({ code: "AUTH_EXPIRED", retryable: false });

    // The provider owns the token lifecycle; classification is read-only.
    expect(nativeCsrfToken()).toBe(TOKEN);
  });

  it("does not supersede a deferred identity refresh", async () => {
    const pending: Array<(response: Response) => void> = [];
    let calls = 0;
    server.use(
      http.get(BOOTSTRAP, () => {
        calls += 1;
        if (calls === 1) {
          return new Promise<Response>((resolve) => {
            pending.push(resolve);
          });
        }
        return HttpResponse.json({
          message: liveMessage({
            sessionEpoch: "probe-epoch",
            csrfToken: "probe-token",
          }),
        });
      }),
    );

    const refresh = fetchNativeSession();
    await waitFor(() => expect(pending).toHaveLength(1));

    await expect(
      classifyNativeForbidden(new AbortController().signal),
    ).resolves.toEqual({ code: "FORBIDDEN", retryable: false });
    expect(nativeCsrfToken()).toBeNull();

    pending[0]?.(
      HttpResponse.json({
        message: liveMessage({
          sessionEpoch: "refresh-epoch",
          csrfToken: "refresh-token",
        }),
      }),
    );
    await expect(refresh).resolves.toMatchObject({
      sessionEpoch: "refresh-epoch",
    });
    expect(nativeCsrfToken()).toBe("refresh-token");
  });

  it("does not invalidate a deferred logout token lookup", async () => {
    const pending: Array<(response: Response) => void> = [];
    const posted: Array<string | null> = [];
    let calls = 0;
    server.use(
      http.get(BOOTSTRAP, () => {
        calls += 1;
        if (calls === 1) {
          return new Promise<Response>((resolve) => {
            pending.push(resolve);
          });
        }
        return HttpResponse.json({
          message: liveMessage({ csrfToken: "probe-token" }),
        });
      }),
      http.post(LOGOUT, ({ request }) => {
        posted.push(request.headers.get(CSRF_HEADER));
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    const logout = nativeLogout();
    await waitFor(() => expect(pending).toHaveLength(1));

    await expect(
      classifyNativeForbidden(new AbortController().signal),
    ).resolves.toEqual({ code: "FORBIDDEN", retryable: false });

    pending[0]?.(HttpResponse.json({ message: liveMessage() }));
    await expect(logout).resolves.toBeUndefined();
    expect(posted).toEqual([TOKEN]);
  });
});

describe("native login", () => {
  it("posts JSON credentials to the Frappe login method and returns the bootstrapped session", async () => {
    const received: Request[] = [];
    server.use(
      http.post(LOGIN, async ({ request }) => {
        received.push(request);
        return HttpResponse.json({ message: "Logged In" });
      }),
      http.get(BOOTSTRAP, () => HttpResponse.json({ message: liveMessage() })),
    );
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(Document.prototype, "cookie", "set");

    const session = await nativeLogin("doctor@example.test", "secret");

    expect(session.mode).toBe("live");
    expect(session.user.roles).toEqual(["DOC"]);
    expect(received).toHaveLength(1);
    const request = received[0];
    expect(request?.credentials).toBe("same-origin");
    expect(request?.headers.get("content-type")).toBe("application/json");
    // Guest login carries no session CSRF token.
    expect(request?.headers.get(CSRF_HEADER)).toBeNull();
    expect(await request?.clone().json()).toEqual({
      usr: "doctor@example.test",
      pwd: "secret",
    });
    expect(storage).not.toHaveBeenCalled();
    expect(cookie).not.toHaveBeenCalled();
  });

  it("maps rejected credentials to FORBIDDEN without upstream text", async () => {
    server.use(
      http.post(LOGIN, () =>
        HttpResponse.json(
          { exc_type: "AuthenticationError", exception: "secret trace" },
          { status: 401 },
        ),
      ),
    );
    await expect(nativeLogin("u", "p")).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
  });

  it.each([[{ message: "ok" }], [{}], [{ message: { mode: "live" } }]])(
    "rejects an unrecognised login body %j",
    async (body) => {
      server.use(
        http.post(LOGIN, () => HttpResponse.json(body)),
        http.get(BOOTSTRAP, () =>
          HttpResponse.json({ message: liveMessage() }),
        ),
      );
      await expect(nativeLogin("u", "p")).rejects.toMatchObject({
        code: "UPSTREAM_UNAVAILABLE",
      });
    },
  );

  it("does not invent a session when the bootstrap follow-up fails", async () => {
    server.use(
      http.post(LOGIN, () => HttpResponse.json({ message: "Logged In" })),
      http.get(BOOTSTRAP, () => new HttpResponse(null, { status: 500 })),
    );
    await expect(nativeLogin("u", "p")).rejects.toMatchObject({
      code: "UPSTREAM_UNAVAILABLE",
    });
    expect(nativeCsrfToken()).toBeNull();
  });
});

describe("native logout", () => {
  it("sends the session CSRF token and clears it afterwards", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    const seen: Array<string | null> = [];
    server.use(
      http.post(LOGOUT, ({ request }) => {
        seen.push(request.headers.get(CSRF_HEADER));
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    await nativeLogout();

    expect(seen).toEqual([TOKEN]);
    expect(nativeCsrfToken()).toBeNull();
  });

  it("re-reads the token once when memory is empty, then logs out once", async () => {
    const calls: string[] = [];
    server.use(
      http.get(BOOTSTRAP, () => {
        calls.push("bootstrap");
        return HttpResponse.json({ message: liveMessage() });
      }),
      http.post(LOGOUT, ({ request }) => {
        calls.push(`logout:${request.headers.get(CSRF_HEADER)}`);
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    await nativeLogout();

    expect(calls).toEqual(["bootstrap", `logout:${TOKEN}`]);
  });

  it("resolves without a POST when there is no session to end", async () => {
    const posted = vi.fn();
    server.use(
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
      ),
      http.post(LOGOUT, () => {
        posted();
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    await expect(nativeLogout()).resolves.toBeUndefined();
    expect(posted).not.toHaveBeenCalled();
  });

  it("reports a rejected CSRF token and never pretends the session ended", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    server.use(
      http.post(LOGOUT, () =>
        HttpResponse.json({ exc_type: "CSRFTokenError" }, { status: 403 }),
      ),
    );

    await expect(nativeLogout()).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
    // A rejected token must never be reused for another attempt.
    expect(nativeCsrfToken()).toBeNull();
  });

  it("clears the token on a Frappe 400 CSRF rejection without a retry", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    const posts = vi.fn();
    server.use(
      http.post(LOGOUT, () => {
        posts();
        return HttpResponse.json(
          { exc_type: "CSRFTokenError", exception: "private trace" },
          { status: 400 },
        );
      }),
    );

    await expect(nativeLogout()).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
    expect(posts).toHaveBeenCalledTimes(1);
    expect(nativeCsrfToken()).toBeNull();
  });

  it("keeps the token when logout fails for a reason other than CSRF", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    server.use(
      http.post(LOGOUT, () =>
        HttpResponse.json({ exc_type: "ValidationError" }, { status: 400 }),
      ),
    );

    await expect(nativeLogout()).rejects.toEqual({
      code: "VALIDATION",
      retryable: false,
    });
    expect(nativeCsrfToken()).toBe(TOKEN);
  });

  it("fails safely without a POST when credentials are invalidated after the lookup", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    const posts = vi.fn();
    server.use(
      http.post(LOGOUT, () => {
        posts();
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    const pending = nativeLogout();
    // Invalidation lands between the token lookup and the request.
    clearNativeCsrfToken();

    // Not a silent success: the caller must treat the logout as not performed.
    await expect(pending).rejects.toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
    expect(posts).not.toHaveBeenCalled();
    expect(nativeCsrfToken()).toBeNull();
  });

  it("aborts without a POST when a pending token read is invalidated", async () => {
    const pendingReads: Array<(response: Response) => void> = [];
    const posts = vi.fn();
    server.use(
      http.get(
        BOOTSTRAP,
        () =>
          new Promise<Response>((resolve) => {
            pendingReads.push(resolve);
          }),
      ),
      http.post(LOGOUT, () => {
        posts();
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    const pending = nativeLogout();
    await waitFor(() => expect(pendingReads).toHaveLength(1));
    clearNativeCsrfToken();
    pendingReads[0]?.(HttpResponse.json({ message: liveMessage() }));

    await expect(pending).rejects.toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
    expect(posts).not.toHaveBeenCalled();
  });

  it("does not retry an uncertain logout after a network failure", async () => {
    serveBootstrap(liveMessage());
    await fetchNativeSession();
    const posts = vi.fn();
    server.use(
      http.post(LOGOUT, () => {
        posts();
        return HttpResponse.error();
      }),
    );

    await expect(nativeLogout()).rejects.toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
    expect(posts).toHaveBeenCalledTimes(1);
    // The session may still exist upstream, so the token stays for a retry.
    expect(nativeCsrfToken()).toBe(TOKEN);
  });
});

describe("native directory", () => {
  it("unwraps the message envelope for the shared directory validator", async () => {
    const items = [
      {
        workspace_patient_id: "workspace-1",
        display_name: "Native Example",
      },
    ];
    server.use(
      http.get(DIRECTORY, () => HttpResponse.json({ message: { items } })),
    );

    await expect(
      fetchNativeDirectoryBody(new AbortController().signal),
    ).resolves.toEqual({ items });
  });

  it.each([
    [401, "AUTH_EXPIRED", false],
    [404, "UNSUPPORTED", false],
  ] as const)(
    "maps HTTP %s to %s without retry",
    async (status, code, retryable) => {
      server.use(
        http.get(DIRECTORY, () =>
          HttpResponse.json({ exc_type: "Error" }, { status }),
        ),
      );
      await expect(
        fetchNativeDirectoryBody(new AbortController().signal),
      ).rejects.toEqual({ code, retryable });
    },
  );

  it("maps a 403 from a guest/expired session to AUTH_EXPIRED", async () => {
    server.use(
      http.get(DIRECTORY, () =>
        HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
      ),
      http.get(BOOTSTRAP, () =>
        HttpResponse.json(
          { exc_type: "AuthenticationError" },
          { status: 401 },
        ),
      ),
    );
    await expect(
      fetchNativeDirectoryBody(new AbortController().signal),
    ).rejects.toEqual({ code: "AUTH_EXPIRED", retryable: false });
  });

  it("keeps a 403 as FORBIDDEN when bootstrap returns an authenticated session", async () => {
    serveBootstrap(liveMessage());
    server.use(
      http.get(DIRECTORY, () =>
        HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
      ),
    );
    await expect(
      fetchNativeDirectoryBody(new AbortController().signal),
    ).rejects.toEqual({ code: "FORBIDDEN", retryable: false });
  });

  it.each([[{}], [{ message: "no items" }]])(
    "rejects a body %j without a Frappe message envelope",
    async (body) => {
      server.use(http.get(DIRECTORY, () => HttpResponse.json(body)));
      await expect(
        fetchNativeDirectoryBody(new AbortController().signal),
      ).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
    },
  );
});
