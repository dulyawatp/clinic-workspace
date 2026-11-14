import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { RequireSession } from "../../app/guards";
import { RETURN_TO_KEY } from "../../app/return-to";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { useReadQuery } from "../../transport/read/query";
import { ProfilePage } from "./ProfilePage";

let client: QueryClient;
function Controller() {
  client = useQueryClient();
  const { status, session, refresh, logout } = useSession();
  const location = useLocation();
  const [outcome, setOutcome] = useState("idle");
  return (
    <>
      <output data-testid="identity">
        {status}:{session?.user.id ?? "none"}
      </output>
      <output data-testid="epoch">{session?.sessionEpoch}</output>
      <output data-testid="path">{location.pathname}</output>
      <output data-testid="logout-outcome">{outcome}</output>
      <Link to={ROUTES.home}>Go home</Link>
      <Link to={ROUTES.profile}>Go profile</Link>
      <button type="button" onClick={() => void refresh()}>
        New session check
      </button>
      <button
        type="button"
        onClick={() => {
          void logout().then(
            (result) => setOutcome(result.status),
            (cause: unknown) => {
              const code =
                typeof cause === "object" && cause !== null && "code" in cause
                  ? String(cause.code)
                  : "UNKNOWN";
              setOutcome(`rejected:${code}`);
            },
          );
        }}
      >
        Logout from shell
      </button>
    </>
  );
}
function ProtectedRows() {
  const query = useReadQuery<{ id: string }>("patients");
  return (
    <p>
      {query.isError
        ? "Read failed"
        : query.data?.items.map((row) => row.id).join(",")}
    </p>
  );
}
function LifetimeRoutes() {
  return (
    <>
      <Controller />
      <Routes>
        <Route path={ROUTES.profile} element={<ProfilePage />} />
        <Route
          path={ROUTES.home}
          element={
            <RequireSession>
              <ProtectedRows />
            </RequireSession>
          }
        />
        <Route path={ROUTES.login} element={<p>Signed out safely</p>} />
      </Routes>
    </>
  );
}
function DeferredProfileRoutes() {
  const { logout } = useSession();
  const [showProfile, setShowProfile] = useState(false);
  const [outcome, setOutcome] = useState("idle");
  return (
    <>
      <output data-testid="late-outcome">{outcome}</output>
      <button
        type="button"
        onClick={() => {
          void logout().then(
            (result) => setOutcome(result.status),
            (cause: unknown) => {
              const code =
                typeof cause === "object" && cause !== null && "code" in cause
                  ? String(cause.code)
                  : "UNKNOWN";
              setOutcome(`rejected:${code}`);
            },
          );
        }}
      >
        Logout offline
      </button>
      <button type="button" onClick={() => setShowProfile(true)}>
        Show profile
      </button>
      <Routes>
        <Route
          path={ROUTES.profile}
          element={showProfile ? <ProfilePage /> : <p>No profile surface</p>}
        />
      </Routes>
    </>
  );
}
afterEach(() => {
  sessionStorage.removeItem(RETURN_TO_KEY);
  vi.restoreAllMocks();
});

it("logout completion after leaving profile still clears protected identity and cached reads", async () => {
  renderWithFixtures(<LifetimeRoutes />, {
    role: "REC",
    path: ROUTES.profile,
    profileCompany: "Old company",
  });
  await screen.findByText("Old company");
  const profileKey = client
    .getQueryCache()
    .find({ queryKey: ["profile"], exact: false })?.queryKey;
  expect(profileKey).toBeDefined();
  if (!profileKey) throw new Error("Missing profile cache key");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  let invalidated = false;
  let releaseCheck!: () => void;
  const checking = new Promise<void>((resolve) => {
    releaseCheck = resolve;
  });
  let checkStarted = false;
  server.use(
    http.post("/api/auth/logout", async () => {
      started = true;
      await held;
      invalidated = true;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("/api/session", async () => {
      checkStarted = true;
      await checking;
      return new HttpResponse(null, { status: invalidated ? 401 : 500 });
    }),
    http.get("/api/read/patients", () =>
      HttpResponse.json({ items: [{ id: "Old protected row" }], total: 1 }),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
  await waitFor(() => expect(started).toBe(true));
  // The initiated logout revokes identity and cache before the POST resolves.
  expect(screen.getByTestId("identity")).toHaveTextContent("loading:none");
  expect(client.getQueryData(profileKey)).toBeUndefined();
  fireEvent.click(screen.getByRole("link", { name: "Go home" }));
  expect(screen.queryByText("Old protected row")).toBeNull();
  expect(client.getQueryCache().getAll()).toHaveLength(0);
  await act(async () => {
    release();
  });
  await waitFor(() => expect(checkStarted).toBe(true));
  expect(screen.getByTestId("identity")).toHaveTextContent("loading:none");
  expect(screen.queryByText("Old protected row")).toBeNull();
  expect(client.getQueryData(profileKey)).toBeUndefined();
  expect(client.getQueryCache().getAll()).toHaveLength(0);
  await act(async () => {
    releaseCheck();
  });
  expect(await screen.findByText("Signed out safely")).toBeVisible();
  expect(screen.getByTestId("identity")).toHaveTextContent("anonymous:none");
  expect(screen.queryByText("Old protected row")).toBeNull();
  expect(client.getQueryData(profileKey)).toBeUndefined();
  expect(client.getQueryCache().getAll()).toHaveLength(0);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
  ).toHaveLength(1);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
});

it.each(["different user", "same user"])(
  "late logout cannot replace newer session for %s",
  async (replacement) => {
    renderWithFixtures(<LifetimeRoutes />, {
      role: "REC",
      path: ROUTES.profile,
      profileCompany: "Old company",
    });
    await screen.findByText("Old company");
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let started = false;
    const userId = replacement === "same user" ? "fixture-rec" : "new-staff";
    server.use(
      http.post("/api/auth/logout", async () => {
        started = true;
        await held;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/session", () =>
        HttpResponse.json({
          sessionEpoch: "new-session",
          user: { id: userId, displayName: "New staff", roles: ["REC"] },
          capabilities: {},
        }),
      ),
      http.get("/api/read/patients", () =>
        HttpResponse.json({ items: [{ id: "New protected row" }], total: 1 }),
      ),
    );
    const requests = vi.spyOn(globalThis, "fetch");
    fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
    await waitFor(() => expect(started).toBe(true));
    fireEvent.click(screen.getByRole("link", { name: "Go home" }));
    fireEvent.click(screen.getByRole("button", { name: "New session check" }));
    await waitFor(() =>
      expect(screen.getByTestId("identity")).toHaveTextContent(
        `authenticated:${userId}`,
      ),
    );
    await screen.findByText("New protected row");
    const patientKey = client
      .getQueryCache()
      .find({ queryKey: ["patients"], exact: false })?.queryKey;
    expect(patientKey).toBeDefined();
    if (!patientKey) throw new Error("Missing patient cache key");
    const cached = client.getQueryData(patientKey);
    const beforeRelease = requests.mock.calls.length;
    await act(async () => {
      release();
    });
    expect(screen.getByTestId("identity")).toHaveTextContent(
      `authenticated:${userId}`,
    );
    expect(screen.getByTestId("path")).toHaveTextContent(ROUTES.home);
    expect(screen.getByText("New protected row")).toBeVisible();
    expect(client.getQueryData(patientKey)).toBe(cached);
    expect(screen.queryByText("Signed out safely")).toBeNull();
    expect(requests.mock.calls.slice(beforeRelease)).toHaveLength(0);
  },
);

it.each(["401", "network"])(
  "same-session refresh cannot suppress logout reconciliation (%s)",
  async (outcome) => {
    renderWithFixtures(<LifetimeRoutes />, {
      role: "REC",
      path: ROUTES.profile,
      profileCompany: "Old company",
    });
    await screen.findByText("Old company");
    const sessionEpoch = screen.getByTestId("epoch").textContent;
    expect(sessionEpoch).toBeTruthy();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let started = false;
    let invalidated = false;
    let failCheck = outcome === "network";
    server.use(
      http.post("/api/auth/logout", async () => {
        started = true;
        await held;
        invalidated = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/session", () =>
        invalidated
          ? failCheck
            ? HttpResponse.error()
            : new HttpResponse(null, { status: 401 })
          : HttpResponse.json({
              sessionEpoch,
              user: {
                id: "fixture-rec",
                displayName: "Same staff",
                roles: ["REC"],
              },
              capabilities: {},
            }),
      ),
      http.get("/api/read/patients", () =>
        HttpResponse.json({ items: [{ id: "Repopulated row" }], total: 1 }),
      ),
    );
    const requests = vi.spyOn(globalThis, "fetch");
    fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
    await waitFor(() => expect(started).toBe(true));
    fireEvent.click(screen.getByRole("link", { name: "Go home" }));
    // Rows stay revoked until the server confirms a session again.
    expect(screen.queryByText("Repopulated row")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New session check" }));
    await waitFor(() =>
      expect(screen.getByTestId("identity")).toHaveTextContent(
        "authenticated:fixture-rec",
      ),
    );
    await screen.findByText("Repopulated row");
    const key = client
      .getQueryCache()
      .find({ queryKey: ["patients"], exact: false })?.queryKey;
    if (!key) throw new Error("Missing patient cache key");
    expect(client.getQueryData(key)).toBeDefined();
    await act(async () => {
      release();
    });
    if (outcome === "network") {
      await screen.findByRole("alert");
      expect(screen.getByTestId("identity")).toHaveTextContent("error:none");
      expect(screen.queryByText("Repopulated row")).toBeNull();
      expect(client.getQueryData(key)).toBeUndefined();
      failCheck = false;
      fireEvent.click(
        screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }),
      );
    }
    expect(await screen.findByText("Signed out safely")).toBeVisible();
    expect(screen.getByTestId("identity")).toHaveTextContent("anonymous:none");
    expect(screen.queryByText("Repopulated row")).toBeNull();
    expect(client.getQueryData(key)).toBeUndefined();
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
    ).toHaveLength(1);
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/session"),
    ).toHaveLength(outcome === "network" ? 3 : 2);
  },
);

it("failed logout feedback survives the revoke unmount and is never replayed after navigation", async () => {
  renderWithFixtures(<LifetimeRoutes />, {
    role: "REC",
    path: ROUTES.profile,
    profileCompany: "Old company",
  });
  await screen.findByText("Old company");
  const sessionEpoch = screen.getByTestId("epoch").textContent;
  expect(sessionEpoch).toBeTruthy();
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  server.use(
    http.post("/api/auth/logout", async () => {
      started = true;
      await held;
      return HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      );
    }),
    // The same session still exists upstream: reconciliation restores it.
    http.get("/api/session", () =>
      HttpResponse.json({
        sessionEpoch,
        user: { id: "fixture-rec", displayName: "Same staff", roles: ["REC"] },
        capabilities: {},
      }),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
  await waitFor(() => expect(started).toBe(true));
  // Revocation precedes the request, so the page that initiated logout is gone.
  expect(screen.getByTestId("identity")).toHaveTextContent("loading:none");
  expect(screen.queryByTestId("profile-name")).toBeNull();
  await act(async () => {
    release();
  });
  // Same-session reconciliation restores the profile and the safe failure still
  // reaches the user instead of being swallowed by the unmounted page.
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "บัญชีนี้ไม่มีสิทธิ์ดำเนินการนี้",
  );
  expect(screen.queryByText("private trace")).toBeNull();
  expect(screen.getByTestId("identity")).toHaveTextContent(
    "authenticated:fixture-rec",
  );
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
  ).toHaveLength(1);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
  // The one-shot feedback is consumed: it cannot reappear on another view.
  fireEvent.click(screen.getByRole("link", { name: "Go home" }));
  expect(screen.getByTestId("path")).toHaveTextContent(ROUTES.home);
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Go profile" }));
  expect(await screen.findByTestId("profile-name")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("failed logout feedback never reaches a different session identity", async () => {
  renderWithFixtures(<LifetimeRoutes />, {
    role: "REC",
    path: ROUTES.profile,
    profileCompany: "Old company",
  });
  await screen.findByText("Old company");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  server.use(
    http.post("/api/auth/logout", async () => {
      started = true;
      await held;
      return HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      );
    }),
    http.get("/api/session", () =>
      HttpResponse.json({
        sessionEpoch: "new-session",
        user: { id: "new-staff", displayName: "New staff", roles: ["REC"] },
        capabilities: {},
      }),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
  await waitFor(() => expect(started).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "New session check" }));
  await waitFor(() =>
    expect(screen.getByTestId("identity")).toHaveTextContent(
      "authenticated:new-staff",
    ),
  );
  await act(async () => {
    release();
  });
  await screen.findByTestId("profile-name");
  // The failed logout of the previous identity is superseded, not reported here.
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("private trace")).toBeNull();
  expect(screen.getByTestId("identity")).toHaveTextContent(
    "authenticated:new-staff",
  );
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
  ).toHaveLength(1);
});

it("logout failure is superseded when reconciliation itself confirms another identity", async () => {
  renderWithFixtures(<LifetimeRoutes />, {
    role: "REC",
    path: ROUTES.profile,
    profileCompany: "Old company",
  });
  await screen.findByText("Old company");
  expect(screen.getByTestId("logout-outcome")).toHaveTextContent("idle");
  server.use(
    http.post("/api/auth/logout", () =>
      HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      ),
    ),
    // No concurrent session check: the reconciliation GET itself confirms a
    // replacement identity, so this failure belongs to nobody left here.
    http.get("/api/session", () =>
      HttpResponse.json({
        sessionEpoch: "replacement-session",
        user: { id: "new-staff", displayName: "New staff", roles: ["REC"] },
        capabilities: {},
      }),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  fireEvent.click(screen.getByRole("button", { name: "Logout from shell" }));
  await waitFor(() =>
    expect(screen.getByTestId("logout-outcome")).toHaveTextContent(
      "superseded",
    ),
  );
  expect(screen.getByTestId("identity")).toHaveTextContent(
    "authenticated:new-staff",
  );
  expect(await screen.findByTestId("profile-name")).toBeVisible();
  // The replacement identity never sees feedback from the previous one.
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("private trace")).toBeNull();
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
  ).toHaveLength(1);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
});

it("retains a same-identity logout failure with no profile mounted during reconciliation", async () => {
  renderWithFixtures(<DeferredProfileRoutes />, {
    role: "REC",
    path: ROUTES.profile,
  });
  await screen.findByText("No profile surface");
  server.use(
    http.post("/api/auth/logout", () =>
      HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      ),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Logout offline" }));
  await waitFor(() =>
    expect(screen.getByTestId("late-outcome")).toHaveTextContent(
      "rejected:FORBIDDEN",
    ),
  );
  // Retention must not depend on a profile element surviving revocation: the
  // real router unmounts the whole protected shell while loading.
  fireEvent.click(screen.getByRole("button", { name: "Show profile" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "บัญชีนี้ไม่มีสิทธิ์ดำเนินการนี้",
  );
  expect(screen.queryByText("private trace")).toBeNull();
});

it("successful logout leaves no failure feedback behind", async () => {
  renderWithFixtures(<LifetimeRoutes />, {
    role: "REC",
    path: ROUTES.profile,
  });
  await screen.findByRole("button", { name: "ออกจากระบบ" });
  fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
  expect(await screen.findByText("Signed out safely")).toBeVisible();
  expect(screen.getByTestId("identity")).toHaveTextContent("anonymous:none");
  expect(screen.queryByRole("alert")).toBeNull();
});
