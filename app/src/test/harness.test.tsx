import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { render, screen } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ROUTES } from "../app/routes";
import { useSession } from "../app/SessionProvider";
import { resetFixtureHandlers } from "./msw/server";
import { allowTestOrigin } from "./network";
import {
  renderAt,
  renderWithCaps,
  renderWithFixtures,
  setViewport,
  withFixtures,
} from "./render";

function Probe() {
  const { status, session } = useSession();
  return (
    <span>
      {status}:{session?.user.roles.join(",") ?? "-"}
    </span>
  );
}

function RouteProbe() {
  const location = useLocation();
  const { status, session } = useSession();
  return (
    <span>
      {location.pathname}|{status}:{session?.user.roles.join(",") ?? "-"}
    </span>
  );
}

async function readJson(path: string) {
  const response = await fetch(path);
  return {
    status: response.status,
    body: (await response.json()) as Record<string, unknown>,
  };
}

describe("test harness", () => {
  it("serves an authenticated session for a fixture role", async () => {
    renderWithFixtures(<Probe />, { role: "DOC" });
    expect(await screen.findByText("authenticated:DOC")).toBeVisible();
  });

  it("serves an anonymous session when authed is false", async () => {
    renderWithFixtures(<Probe />, { authed: false });
    expect(await screen.findByText("anonymous:-")).toBeVisible();
  });

  it("enforces the CSRF header on MSW login and logout", async () => {
    renderWithFixtures(<Probe />, { role: "REC" });
    const login = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ usr: "rec", pwd: "fixture" }),
    });
    expect(login.status).toBe(403);
    const logout = await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    expect(logout.status).toBe(403);
  });

  it("renders capability-only components without a session provider", () => {
    renderWithCaps({ "read.lab": "denied" }, <span>caps</span>);
    expect(screen.getByText("caps")).toBeVisible();
  });

  it("does not invent routes outside ROUTES", () => {
    renderAt(ROUTES.search, <span>search</span>);
    expect(screen.getByText("search")).toBeVisible();
  });

  it("sets a viewport width for responsive assertions", () => {
    setViewport(834);
    expect(window.innerWidth).toBe(834);
    expect(window.matchMedia("(min-width: 768px)").matches).toBe(true);
    expect(window.matchMedia("(min-width: 1280px)").matches).toBe(false);
  });

  it("restores the default viewport after the previous test", () => {
    expect(window.innerWidth).toBe(1024);
    expect(window.matchMedia("(min-width: 1280px)").matches).toBe(false);
  });

  it("blocks unexpected non-loopback network requests", async () => {
    const blocked = await fetch("https://clinic.example/api/session");
    expect(blocked.status).toBe(500);
    expect(blocked.statusText).toBe("Unhandled Exception");
    expect(await blocked.text()).toMatch(/Blocked unhandled network request/);
  });

  it("blocks an unhandled relative endpoint before any socket", async () => {
    const blocked = await fetch("/api/not-a-resource");
    expect(blocked.status).toBe(500);
    expect(blocked.statusText).toBe("Unhandled Exception");
    expect(await blocked.text()).toMatch(
      /Blocked unhandled network request during tests: http:\/\/localhost:5173\/api\/not-a-resource/,
    );
  });

  it("blocks an unregistered loopback origin without contacting it", async () => {
    let received = 0;
    const local = createServer((_req, res) => {
      received += 1;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
    });
    await new Promise<void>((resolve) => {
      local.listen(0, "127.0.0.1", resolve);
    });
    const { port } = local.address() as AddressInfo;

    try {
      const blocked = await fetch(`http://127.0.0.1:${port}/api/session`);
      expect(blocked.status).toBe(500);
      expect(blocked.statusText).toBe("Unhandled Exception");
      expect(await blocked.text()).toMatch(/Blocked unhandled network request/);
      expect(received).toBe(0);
    } finally {
      await new Promise<void>((resolve) => {
        local.close(() => resolve());
      });
    }
  });

  it("blocks an origin again once its registration is revoked", async () => {
    let received = 0;
    const local = createServer((_req, res) => {
      received += 1;
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });
    await new Promise<void>((resolve) => {
      local.listen(0, "127.0.0.1", resolve);
    });
    const { port } = local.address() as AddressInfo;
    const origin = `http://127.0.0.1:${port}`;
    const release = allowTestOrigin(origin);

    try {
      const allowed = await fetch(`${origin}/api/session`);
      expect(allowed.status).toBe(200);
      expect(received).toBe(1);

      release();
      const blocked = await fetch(`${origin}/api/session`);
      expect(blocked.status).toBe(500);
      expect(await blocked.text()).toMatch(/Blocked unhandled network request/);
      expect(received).toBe(1);
    } finally {
      release();
      await new Promise<void>((resolve) => {
        local.close(() => resolve());
      });
    }
  });

  it("resets providers and handlers together on a same-scenario rerender", async () => {
    const view = render(withFixtures(<Probe />, { role: "REC" }));
    expect(await screen.findByText("authenticated:REC")).toBeVisible();

    const csrf = await fetch("/api/auth/csrf");
    const { token } = (await csrf.json()) as { token: string };
    const logout = await fetch("/api/auth/logout", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-clinic-csrf": token,
      },
    });
    expect(logout.status).toBe(204);
    expect((await fetch("/api/session")).status).toBe(401);

    // Same scenario, new call: providers remount and handlers are fresh, so
    // the UI session and the server-side handler state never disagree.
    view.rerender(withFixtures(<Probe />, { role: "REC" }));
    expect(await screen.findByText("authenticated:REC")).toBeVisible();
    expect((await fetch("/api/session")).status).toBe(200);
  });

  it("starts each test from a fresh anonymous baseline after a login", async () => {
    const csrf = await fetch("/api/auth/csrf");
    const { token } = (await csrf.json()) as { token: string };
    const login = await fetch("/api/auth/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-clinic-csrf": token,
      },
      body: JSON.stringify({ usr: "doc", pwd: "fixture" }),
    });
    expect(login.status).toBe(200);
    const authed = await fetch("/api/session");
    expect(authed.status).toBe(200);
    expect(
      ((await authed.json()) as { user: { roles: string[] } }).user.roles,
    ).toEqual(["DOC"]);

    resetFixtureHandlers();
    const anonymous = await fetch("/api/session");
    expect(anonymous.status).toBe(401);
  });

  it("is still anonymous in the test after the previous login", async () => {
    const session = await fetch("/api/session");
    expect(session.status).toBe(401);
  });

  it("remounts providers and resets the session when rerender changes scenario", async () => {
    const view = render(withFixtures(<RouteProbe />, { role: "REC" }));
    expect(
      await screen.findByText(`${ROUTES.home}|authenticated:REC`),
    ).toBeVisible();

    view.rerender(withFixtures(<RouteProbe />, { role: "DOC" }));
    expect(
      await screen.findByText(`${ROUTES.home}|authenticated:DOC`),
    ).toBeVisible();
    // The DOC handler scenario is the active one, not a leftover REC closure.
    const changed = await fetch("/api/session");
    expect(changed.status).toBe(200);
    expect(
      ((await changed.json()) as { user: { roles: string[] } }).user.roles,
    ).toEqual(["DOC"]);

    view.rerender(withFixtures(<RouteProbe />, { authed: false }));
    expect(await screen.findByText(`${ROUTES.home}|anonymous:-`)).toBeVisible();
    expect((await fetch("/api/session")).status).toBe(401);

    view.rerender(withFixtures(<RouteProbe />, { path: ROUTES.search }));
    expect(
      await screen.findByText(`${ROUTES.search}|authenticated:REC`),
    ).toBeVisible();
  });

  it("requires a session before serving read fixtures", async () => {
    renderWithFixtures(<Probe />, { authed: false });
    await screen.findByText("anonymous:-");
    const read = await readJson("/api/read/patients");
    expect(read.status).toBe(401);
  });

  it("returns 404 for an unknown read resource", async () => {
    renderWithFixtures(<Probe />, { role: "REC" });
    const read = await readJson("/api/read/unknown");
    expect(read.status).toBe(404);
  });

  it("filters fixture reads by the unverified role matrix", async () => {
    renderWithFixtures(<Probe />, { role: "PHA" });
    const denied = await readJson("/api/read/patients");
    expect(denied.status).toBe(403);

    const allowed = await readJson("/api/read/profile");
    expect(allowed.status).toBe(200);
  });

  it("pages the 24 fixture patients in 20-row pages", async () => {
    renderWithFixtures(<Probe />, { role: "REC" });
    const first = await readJson("/api/read/patients");
    expect(first.body.total).toBe(24);
    expect(first.body.items).toHaveLength(20);

    const second = await readJson("/api/read/patients?offset=20");
    expect(second.body.items).toHaveLength(4);
  });

  it("matches Thai query text and never exposes internal notes", async () => {
    renderWithFixtures(<Probe />, { role: "REC" });
    const patients = await readJson(
      "/api/read/patients?q=%E0%B8%AA%E0%B8%A1%E0%B8%8A%E0%B8%B2%E0%B8%A2",
    );
    expect(patients.body.total).toBe(1);
    const [patient] = patients.body.items as Array<Record<string, unknown>>;
    expect(patient?.fullName).toBe("สมชาย Smith");
    expect(patient).not.toHaveProperty("internal_notes");
  });

  it("trims q and rejects search on resources without search fields", async () => {
    renderWithFixtures(<Probe />, { role: "REC" });

    const whitespace = await readJson("/api/read/patients?q=%20%20");
    expect(whitespace.status).toBe(200);
    expect(whitespace.body.total).toBe(24);

    const unsupported = await readJson("/api/read/appointments?q=fixture");
    expect(unsupported.status).toBe(400);
    expect(unsupported.body).toEqual({ code: "VALIDATION" });
  });

  it("ignores override keys owned by later plans", async () => {
    renderWithFixtures(<Probe />, { role: "REC", duplicates: true });
    const patients = await readJson("/api/read/patients");
    expect(patients.status).toBe(200);
  });

  it("serves work items for the session role and an empty override", async () => {
    renderWithFixtures(<Probe />, { role: "ADM" });
    const workItems = await readJson("/api/read/work-items");
    expect(workItems.status).toBe(200);
    const items = workItems.body.items as Array<Record<string, unknown>>;
    expect(items.some((item) => item.routeKey === "system")).toBe(true);

    renderWithFixtures(<Probe />, { role: "ADM", workItems: "empty" });
    const empty = await readJson("/api/read/work-items");
    expect(empty.body.items).toEqual([]);
  });

  it("overrides and clears the profile company", async () => {
    renderWithFixtures(<Probe />, {
      role: "REC",
      profileCompany: "Override Co",
    });
    const overridden = await readJson("/api/read/profile");
    expect(
      (overridden.body.items as Array<Record<string, unknown>>)[0]?.company,
    ).toBe("Override Co");

    renderWithFixtures(<Probe />, { role: "REC", profileCompany: null });
    const cleared = await readJson("/api/read/profile");
    expect(cleared.body.items).toEqual([]);
  });
});
