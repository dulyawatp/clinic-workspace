import { describe, expect, it, vi } from "vitest";
import type { LiveUpstream } from "../config.js";
import { createLiveAuthenticate } from "./live-auth.js";

const UPSTREAM: LiveUpstream = {
  origin: "https://lab.example",
  timeoutMs: 1_000,
  syntheticPatients: new Map([["PAT-1", "SYNTH-PATIENT-1"]]),
  clinicianRoles: new Set(["Clinic Clinician"]),
};

type Handler = (url: string, init: RequestInit) => Response;

function fetchImpl(handler: Handler) {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) =>
    handler(String(input), init ?? {}),
  ) as unknown as typeof fetch;
}

function jsonOk(body: unknown, cookies: string[] = []): Response {
  const headers = new Headers({ "content-type": "application/json" });
  for (const cookie of cookies) headers.append("set-cookie", cookie);
  return new Response(JSON.stringify(body), { status: 200, headers });
}

function deskHtml(token: string): Response {
  return new Response(
    `<html><script>frappe.csrf_token = "${token}";</script></html>`,
    { status: 200, headers: { "content-type": "text/html" } },
  );
}

function loginSuccess(
  overrides: {
    cookie?: string;
    roles?: string[];
    csrf?: string;
    deskStatus?: number;
  } = {},
): Handler {
  return (url) => {
    if (url.endsWith("/api/method/login")) {
      return jsonOk({ message: "Logged In", full_name: "Dr Lab" }, [
        overrides.cookie ?? "sid=native-sid; Path=/; HttpOnly",
      ]);
    }
    if (url.endsWith("/api/method/frappe.auth.get_logged_user")) {
      return jsonOk({ message: "clinician@lab.example" });
    }
    if (
      url.endsWith(
        "/api/method/frappe.core.doctype.user.user.get_current_user_roles",
      )
    ) {
      return jsonOk({
        message: overrides.roles ?? ["Clinic Clinician", "Desk User"],
      });
    }
    if (url.endsWith("/desk")) {
      if (overrides.deskStatus !== undefined) {
        return new Response("no", { status: overrides.deskStatus });
      }
      return deskHtml(overrides.csrf ?? "native-csrf-token");
    }
    throw new Error(`unexpected url ${url}`);
  };
}

describe("createLiveAuthenticate", () => {
  it("maps the dedicated admin marker to ADM", async () => {
    const upstream = { ...UPSTREAM, adminRoles: new Set(["QA ADM"]) };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["QA ADM"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["ADM"]);
  });
  it("maps a cashier marker to CAS", async () => {
    const upstream = { ...UPSTREAM, cashierRoles: new Set(["QA CAS"]) };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["QA CAS"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["CAS"]);
  });
  it("maps the dedicated nurse marker to NUR instead of DOC", async () => {
    const upstream = { ...UPSTREAM, nurseRoles: new Set(["QA NUR"]) };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["QA NUR", "Clinic Clinician"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["NUR"]);
  });
  it("maps the configured owner role to MGR even when a clinician role is also present", async () => {
    const upstream = { ...UPSTREAM, managerRoles: new Set(["DC Owner"]) };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["DC Owner", "Clinic Clinician"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["MGR"]);
  });
  it("maps configured pharmacy to PHA rather than clinician", async () => {
    const upstream = { ...UPSTREAM, pharmacyRoles: new Set(["DC Pharmacy"]) };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["DC Pharmacy", "Clinic Clinician"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["PHA"]);
  });
  it("maps reception before clinician when both native roles are present", async () => {
    const upstream = {
      ...UPSTREAM,
      receptionRoles: new Set(["DC Receptionist"]),
    };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(
        loginSuccess({ roles: ["DC Receptionist", "Clinic Clinician"] }),
      ),
    );
    const result = await auth({ usr: "u", pwd: "p" });
    expect(result?.user.roles).toEqual(["REC"]);
  });
  it("admits a configured reception role without a clinician role", async () => {
    const upstream = {
      ...UPSTREAM,
      receptionRoles: new Set(["DC Receptionist"]),
    };
    const auth = createLiveAuthenticate(
      upstream,
      fetchImpl(loginSuccess({ roles: ["DC Receptionist"] })),
    );
    expect((await auth({ usr: "u", pwd: "p" }))?.user.roles).toEqual(["REC"]);
  });
  it("establishes the clinician's own native session and returns it server-side", async () => {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const base = loginSuccess();
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl((url, init) => {
        seen.push({ url, init });
        return base(url, init);
      }),
    );

    const result = await auth({ usr: "clinician@lab.example", pwd: "secret" });

    expect(result).toEqual({
      user: {
        id: "clinician@lab.example",
        displayName: "Dr Lab",
        roles: ["DOC"],
      },
      native: { sid: "native-sid", csrf: "native-csrf-token" },
    });

    const login = seen[0];
    expect(login?.url).toBe("https://lab.example/api/method/login");
    expect(JSON.parse(String(login?.init.body))).toEqual({
      usr: "clinician@lab.example",
      pwd: "secret",
    });

    // Every later call is bound to the clinician's cookie, never a shared account.
    for (const call of seen.slice(1)) {
      expect((call.init.headers as Record<string, string>).cookie).toBe(
        "sid=native-sid",
      );
    }

    const desk = seen.find((call) => call.url.endsWith("/desk"));
    expect(desk?.init.method).toBe("GET");
    expect(seen.some((call) => call.url.endsWith("/app"))).toBe(false);
  });

  it("returns null for rejected credentials", async () => {
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(() => new Response("{}", { status: 401 })),
    );
    await expect(auth({ usr: "u", pwd: "p" })).resolves.toBeNull();
  });

  it("refuses a session without a configured clinician role", async () => {
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(loginSuccess({ roles: ["Desk User"] })),
    );
    await expect(auth({ usr: "u", pwd: "p" })).resolves.toBeNull();
  });

  it("refuses Administrator even if a configured role is present", async () => {
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(loginSuccess({ roles: ["Clinic Clinician", "Administrator"] })),
    );
    await expect(auth({ usr: "u", pwd: "p" })).resolves.toBeNull();
  });

  it("fails closed when the login response carries no usable sid", async () => {
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(loginSuccess({ cookie: "sid=Guest; Path=/" })),
    );
    await expect(auth({ usr: "u", pwd: "p" })).rejects.toThrow(
      "NATIVE_UPSTREAM_UNAVAILABLE",
    );
  });

  it("fails closed when the native desk token cannot be established", async () => {
    const noMarker = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl((url, init) =>
        url.endsWith("/desk")
          ? new Response("<html>no token</html>", { status: 200 })
          : loginSuccess()(url, init),
      ),
    );
    await expect(noMarker({ usr: "u", pwd: "p" })).rejects.toThrow(
      "NATIVE_UPSTREAM_UNAVAILABLE",
    );

    const deniedDesk = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(loginSuccess({ deskStatus: 403 })),
    );
    await expect(deniedDesk({ usr: "u", pwd: "p" })).rejects.toThrow(
      "NATIVE_UPSTREAM_UNAVAILABLE",
    );

    // A redirect (including `/desk` -> `/desk/...`) is never followed or accepted.
    const redirectedDesk = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(loginSuccess({ deskStatus: 302 })),
    );
    await expect(redirectedDesk({ usr: "u", pwd: "p" })).rejects.toThrow(
      "NATIVE_UPSTREAM_UNAVAILABLE",
    );
  });

  it("never echoes credentials in a transport failure", async () => {
    const auth = createLiveAuthenticate(
      UPSTREAM,
      fetchImpl(() => {
        throw new Error("connect ECONNREFUSED with secret");
      }),
    );
    await expect(auth({ usr: "u", pwd: "hunter2" })).rejects.toThrow(
      /NATIVE_UPSTREAM_UNAVAILABLE/,
    );
    await expect(auth({ usr: "u", pwd: "hunter2" })).rejects.not.toThrow(
      /hunter2|ECONNREFUSED/,
    );
  });
});
