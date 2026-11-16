import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { appRoutes } from "../../app/router";
import { useSession } from "../../app/SessionProvider";
import type { RoleCode } from "../../domain/capability";
import { server } from "../../test/msw/server";
import { renderWithDataRouter, renderWithFixtures } from "../../test/render";
import { postLogin } from "../../transport/auth";
import { PatientProfilePage } from "./PatientProfilePage";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const VIEW = "/api/method/clinic_ui.api.patients.view";

const DETAIL = {
  workspace_patient_id: "WS-1",
  display_name: "Patient One",
  first_name: "Patient",
  last_name: "One",
  sex: "Female",
  dob: "1990-01-02",
  status: "Active",
};

function liveMessage(roles: RoleCode[] = ["DOC"]) {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles },
    capabilities: { "read.patient": "enabled" },
    csrfToken: "native-csrf-token",
  };
}

function serveBootstrap(roles: RoleCode[] = ["DOC"], status = 200) {
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json(
        status === 200
          ? { message: liveMessage(roles) }
          : { exc_type: "AuthenticationError" },
        { status },
      ),
    ),
  );
}

function serveView(body: unknown, status = 200) {
  server.use(
    http.get(VIEW, () => HttpResponse.json({ message: body }, { status })),
  );
}

function RefreshSession() {
  const { refresh } = useSession();
  return (
    <button type="button" onClick={() => void refresh()}>
      Refresh session
    </button>
  );
}

function SessionControls({ alias }: { alias: string }) {
  const { logout, refresh } = useSession();
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => void logout()}>
        Logout
      </button>
      <button
        type="button"
        onClick={async () => {
          await postLogin("doc", "fixture");
          await refresh();
          await navigate(ROUTES.patient(alias));
        }}
      >
        Relogin
      </button>
    </>
  );
}

function IdentityControls({ nextAlias }: { nextAlias?: string }) {
  const { refresh } = useSession();
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => void refresh()}>
        Refresh session
      </button>
      {nextAlias !== undefined && (
        <button
          type="button"
          onClick={() => navigate(ROUTES.patient(nextAlias))}
        >
          Open next
        </button>
      )}
    </>
  );
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve: () => void = () => {};
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

let cacheClient: QueryClient | undefined;

function CacheProbe() {
  cacheClient = useQueryClient();
  return null;
}

/** Every patient detail currently in the query cache, across all identities. */
function cachedDetailNames(): string[] {
  const queries = cacheClient?.getQueryCache().findAll({
    queryKey: ["live-patient"],
  });
  return (queries ?? []).flatMap((query) => {
    const data = query.state.data as { display_name?: unknown } | undefined;
    return typeof data?.display_name === "string" ? [data.display_name] : [];
  });
}

type DeliveryOutcome = "pending" | "aborted" | "delivered";

/**
 * Transport-level settlement for one in-flight view request. It resolves on
 * the client abort signal or when the handler delivers, not merely when the
 * handler body returns.
 */
function trackDelivery(request: Request) {
  const state: { outcome: DeliveryOutcome } = { outcome: "pending" };
  const settled = deferred();
  const mark = (outcome: DeliveryOutcome) => {
    if (state.outcome !== "pending") return;
    state.outcome = outcome;
    settled.resolve();
  };
  if (request.signal.aborted) mark("aborted");
  else
    request.signal.addEventListener("abort", () => mark("aborted"), {
      once: true,
    });
  return { state, settled, delivered: () => mark("delivered") };
}

/** Flush pending microtasks, timers and React updates before asserting. */
async function flushReact(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function renderProfile(alias = "WS-1", role: RoleCode = "DOC") {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  return renderWithFixtures(
    <Routes>
      <Route
        path={`${ROUTES.patients}/:id`}
        element={<PatientProfilePage />}
      />
    </Routes>,
    { role, sessionMode: "live", path: ROUTES.patient(alias) },
  );
}

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

it.each(["DOC", "NUR"] as const)(
  "renders only the allowlisted native fields for an authorized %s",
  async (role) => {
    serveBootstrap([role]);
    serveView(DETAIL);
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    renderProfile();

    expect(await screen.findByText("Patient One")).toBeVisible();
    expect(screen.getByTestId("patient-id")).toHaveTextContent("WS-1");
    expect(screen.getByText("วันเกิด")).toBeVisible();
    expect(screen.getByText("02/01/1990")).toBeVisible();
    expect(screen.getByText("เพศ")).toBeVisible();
    expect(screen.getByText("หญิง")).toBeVisible();
    expect(screen.getByText("สถานะ")).toBeVisible();
    expect(screen.getByText("ใช้งาน")).toBeVisible();
    // The native docname, contact values and clinical extras never appear.
    expect(screen.queryByText(/PAT-1/)).toBeNull();
    expect(screen.queryByText(/081/)).toBeNull();
    expect(screen.queryByText(/allerg|แพ้ยา/i)).toBeNull();
    // No legacy proxy or fixture read is used as a fallback.
    expect(legacyCalls(fetchSpy)).toEqual([]);
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).startsWith("/api/read/")),
    ).toBe(false);
  },
);

it("refuses a receptionist the native view without rendering a record", async () => {
  serveBootstrap(["REC"]);
  serveView({}, 403);
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderProfile();

  expect(
    await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "ข้อมูลผู้ป่วย" })).toBeVisible();
  expect(screen.queryByText("Patient One")).toBeNull();
  expect(screen.queryByTestId("patient-id")).toBeNull();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("preserves unknown native values without treating them as translation keys", async () => {
  serveBootstrap();
  serveView({ ...DETAIL, sex: "__proto__", status: "Pending review" });
  renderProfile();
  expect(await screen.findByText("__proto__")).toBeVisible();
  expect(screen.getByText("Pending review")).toBeVisible();
});

it("fails closed for a non-allowlisted alias", async () => {
  serveBootstrap();
  serveView({}, 403);

  renderProfile("WS-UNKNOWN");

  expect(
    await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeVisible();
  expect(screen.queryByText("Patient One")).toBeNull();
  expect(screen.queryByRole("button", { name: /ลองใหม่|retry/i })).toBeNull();
});

it("fails closed on a malformed alias without any native request", async () => {
  serveBootstrap();
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderProfile(" ");

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(
    fetchSpy.mock.calls.filter(([url]) => String(url).startsWith(VIEW)),
  ).toEqual([]);
});

it.each([
  { ...DETAIL, native_patient_id: "PAT-1" },
  { ...DETAIL, mobile: "0812345678" },
  { ...DETAIL, display_name: "" },
  { ...DETAIL, sex: 5 },
  { ...DETAIL, editable: "true" },
])("rejects a detail payload with extra or malformed fields", async (body) => {
  serveBootstrap();
  serveView(body);

  renderProfile();

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("PAT-1")).toBeNull();
  expect(screen.queryByText("0812345678")).toBeNull();
});

it("refreshes the session on an expired native view", async () => {
  let expired = false;
  let sessionChecks = 0;
  server.use(
    http.get(BOOTSTRAP, () => {
      sessionChecks += 1;
      return expired
        ? HttpResponse.json({}, { status: 401 })
        : HttpResponse.json({ message: liveMessage() });
    }),
    http.get(VIEW, () => {
      expired = true;
      return HttpResponse.json({}, { status: 401 });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderProfile();

  expect(await screen.findByRole("alert")).toBeVisible();
  await userEvent.click(
    await screen.findByRole("button", { name: /ลองใหม่|retry/i }),
  );
  await waitFor(() => expect(sessionChecks).toBe(2));
  expect(
    fetchSpy.mock.calls.filter(([url]) => String(url).startsWith(VIEW)),
  ).toHaveLength(1);
});

it("never requests the native view for a role outside the patient surface", async () => {
  serveBootstrap(["PHA"]);
  const view = vi.fn();
  server.use(http.get(VIEW, view));
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderProfile("WS-1", "PHA");

  expect(
    await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeVisible();
  expect(view).not.toHaveBeenCalled();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("sends an anonymous caller to sign-in without a native view request", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  serveBootstrap([], 401);
  const view = vi.fn();
  server.use(http.get(VIEW, view));

  const { router } = renderWithDataRouter(appRoutes, {
    initialEntries: [ROUTES.patient("WS-1")],
    authed: false,
    sessionMode: "live",
  });

  expect(await screen.findByLabelText("ชื่อผู้ใช้")).toBeVisible();
  await waitFor(() => expect(router.state.location.pathname).toBe(ROUTES.login));
  expect(view).not.toHaveBeenCalled();
});

it("rejects a response whose alias does not match the requested alias", async () => {
  serveBootstrap();
  serveView({
    ...DETAIL,
    workspace_patient_id: "WS-OTHER",
    display_name: "Leaked Name",
  });

  renderProfile("WS-1");

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("Leaked Name")).toBeNull();
  expect(screen.queryByTestId("patient-id")).toBeNull();
});

it("renders a real leap-day birth date", async () => {
  serveBootstrap();
  serveView({ ...DETAIL, dob: "2024-02-29" });

  renderProfile();

  expect(await screen.findByText("29/02/2024")).toBeVisible();
});

it.each([
  "2023-02-29",
  "1990-02-30",
  "1990-2-3",
  "1990-13-01",
  "0000-00-00",
  "not-a-date",
])("fails closed on an impossible birth date %s", async (dob) => {
  serveBootstrap();
  serveView({ ...DETAIL, dob });

  renderProfile();

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText(/NaN|Invalid Date/)).toBeNull();
  expect(screen.queryByText("Patient One")).toBeNull();
});

it("never shows the previous patient after switching records", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  serveBootstrap();
  server.use(
    http.get(VIEW, ({ request }) => {
      const alias = new URL(request.url).searchParams.get(
        "workspace_patient_id",
      );
      return HttpResponse.json({
        message:
          alias === "WS-2"
            ? {
                ...DETAIL,
                workspace_patient_id: "WS-2",
                display_name: "Patient Two",
              }
            : DETAIL,
      });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  const { router } = renderWithDataRouter(appRoutes, {
    initialEntries: [ROUTES.patient("WS-1")],
    role: "DOC",
    sessionMode: "live",
  });

  expect(await screen.findByText("Patient One")).toBeVisible();
  await router.navigate(ROUTES.patient("WS-2"));

  expect(await screen.findByText("Patient Two")).toBeVisible();
  expect(screen.queryByText("Patient One")).toBeNull();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("drops prior demographics when the session identity changes", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  let identity = "identity-one";
  let displayName = "Patient One";
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({
        message: {
          ...liveMessage(),
          sessionEpoch: identity,
          user: { id: identity, displayName: identity, roles: ["DOC"] },
        },
      }),
    ),
    http.get(VIEW, () =>
      HttpResponse.json({ message: { ...DETAIL, display_name: displayName } }),
    ),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderWithFixtures(
    <>
      <RefreshSession />
      <Routes>
        <Route
          path={`${ROUTES.patients}/:id`}
          element={<PatientProfilePage />}
        />
      </Routes>
    </>,
    { role: "DOC", sessionMode: "live", path: ROUTES.patient("WS-1") },
  );

  expect(await screen.findByText("Patient One")).toBeVisible();
  identity = "identity-two";
  displayName = "Patient Two";
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );

  expect(await screen.findByText("Patient Two")).toBeVisible();
  expect(screen.queryByText("Patient One")).toBeNull();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("shows no prior demographics across logout and relogin", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  let loggedOut = false;
  let identity = "identity-one";
  let displayName = "Patient One";
  let viewCalls = 0;
  server.use(
    http.get(BOOTSTRAP, () =>
      loggedOut
        ? HttpResponse.json({}, { status: 401 })
        : HttpResponse.json({
            message: {
              ...liveMessage(),
              sessionEpoch: identity,
              user: { id: identity, displayName: identity, roles: ["DOC"] },
            },
          }),
    ),
    http.post("/api/method/login", () => {
      loggedOut = false;
      identity = "identity-two";
      displayName = "Patient Two";
      return HttpResponse.json({ message: "Logged In" });
    }),
    http.post("/api/method/logout", () => {
      loggedOut = true;
      return HttpResponse.json({ message: "Logged Out" });
    }),
    http.get(VIEW, () => {
      viewCalls += 1;
      return HttpResponse.json({
        message: { ...DETAIL, display_name: displayName },
      });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderWithFixtures(
    <>
      <SessionControls alias="WS-1" />
      <Routes>
        <Route
          path={`${ROUTES.patients}/:id`}
          element={<PatientProfilePage />}
        />
      </Routes>
    </>,
    { role: "DOC", sessionMode: "live", path: ROUTES.patient("WS-1") },
  );

  expect(await screen.findByText("Patient One")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Logout" }));
  await waitFor(() => expect(screen.queryByText("Patient One")).toBeNull());

  await userEvent.click(screen.getByRole("button", { name: "Relogin" }));

  expect(await screen.findByText("Patient Two")).toBeVisible();
  expect(screen.queryByText("Patient One")).toBeNull();
  expect(viewCalls).toBe(2);
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("ignores a delayed response for a previously addressed patient", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  serveBootstrap();
  const slowStarted = deferred();
  const releaseSlow = deferred();
  let slowDelivery: ReturnType<typeof trackDelivery> | undefined;
  let calls = 0;
  server.use(
    http.get(VIEW, async ({ request }) => {
      calls += 1;
      const alias = new URL(request.url).searchParams.get(
        "workspace_patient_id",
      );
      if (calls === 1) {
        slowDelivery = trackDelivery(request);
        slowStarted.resolve();
        await releaseSlow.promise;
        slowDelivery.delivered();
        return HttpResponse.json({
          message: { ...DETAIL, display_name: "Slow Patient" },
        });
      }
      return HttpResponse.json({
        message: {
          ...DETAIL,
          workspace_patient_id: alias,
          display_name: "Fast Patient",
        },
      });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  const { router } = renderWithDataRouter(appRoutes, {
    initialEntries: [ROUTES.patient("WS-1")],
    role: "DOC",
    sessionMode: "live",
  });

  await slowStarted.promise;
  expect(slowDelivery?.state.outcome).toBe("pending");
  await router.navigate(ROUTES.patient("WS-2"));
  expect(await screen.findByText("Fast Patient")).toBeVisible();

  releaseSlow.resolve();
  await waitFor(() =>
    expect(slowDelivery?.state.outcome).not.toBe("pending"),
  );
  await flushReact();

  expect(screen.queryByText("Slow Patient")).toBeNull();
  expect(screen.getByText("Fast Patient")).toBeVisible();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("ignores a delayed response across an identity replacement", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  let identity = "identity-one";
  let calls = 0;
  let oldDelivery: ReturnType<typeof trackDelivery> | undefined;
  const slowStarted = deferred();
  const releaseSlow = deferred();
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({
        message: {
          ...liveMessage(),
          sessionEpoch: identity,
          user: { id: identity, displayName: identity, roles: ["DOC"] },
        },
      }),
    ),
    http.get(VIEW, async ({ request }) => {
      calls += 1;
      if (calls === 1) {
        oldDelivery = trackDelivery(request);
        slowStarted.resolve();
        await releaseSlow.promise;
        oldDelivery.delivered();
        return HttpResponse.json({
          message: { ...DETAIL, display_name: "Old Identity Patient" },
        });
      }
      return HttpResponse.json({
        message: { ...DETAIL, display_name: "New Identity Patient" },
      });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderWithFixtures(
    <>
      <CacheProbe />
      <IdentityControls />
      <Routes>
        <Route
          path={`${ROUTES.patients}/:id`}
          element={<PatientProfilePage />}
        />
      </Routes>
    </>,
    { role: "DOC", sessionMode: "live", path: ROUTES.patient("WS-1") },
  );

  await slowStarted.promise;
  expect(oldDelivery?.state.outcome).toBe("pending");
  identity = "identity-two";
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  expect(await screen.findByText("New Identity Patient")).toBeVisible();
  expect(screen.queryByText("Old Identity Patient")).toBeNull();

  releaseSlow.resolve();
  await waitFor(() =>
    expect(oldDelivery?.state.outcome).not.toBe("pending"),
  );
  await flushReact();

  // The old response either aborted with the superseded query or delivered
  // after it was removed; neither may leave data or cache behind.
  expect(oldDelivery?.state.outcome).toBe("aborted");
  expect(screen.queryByText("Old Identity Patient")).toBeNull();
  expect(screen.getByText("New Identity Patient")).toBeVisible();
  expect(cachedDetailNames()).not.toContain("Old Identity Patient");
  expect(legacyCalls(fetchSpy)).toEqual([]);
});

it("shows no delayed demographics after a role-only revocation", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  let roles: RoleCode[] = ["DOC"];
  let viewCalls = 0;
  let oldDelivery: ReturnType<typeof trackDelivery> | undefined;
  const slowStarted = deferred();
  const releaseSlow = deferred();
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({
        message: { ...liveMessage(roles), sessionEpoch: "identity-one" },
      }),
    ),
    http.get(VIEW, async ({ request }) => {
      viewCalls += 1;
      oldDelivery = trackDelivery(request);
      slowStarted.resolve();
      await releaseSlow.promise;
      oldDelivery.delivered();
      return HttpResponse.json({
        message: { ...DETAIL, display_name: "Role Revoked Patient" },
      });
    }),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  renderWithFixtures(
    <>
      <CacheProbe />
      <IdentityControls />
      <Routes>
        <Route
          path={`${ROUTES.patients}/:id`}
          element={<PatientProfilePage />}
        />
      </Routes>
    </>,
    { role: "DOC", sessionMode: "live", path: ROUTES.patient("WS-1") },
  );

  await slowStarted.promise;
  expect(oldDelivery?.state.outcome).toBe("pending");
  roles = ["PHA"];
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  expect(
    await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeVisible();

  releaseSlow.resolve();
  await waitFor(() =>
    expect(oldDelivery?.state.outcome).not.toBe("pending"),
  );
  await flushReact();

  expect(screen.queryByText("Role Revoked Patient")).toBeNull();
  expect(cachedDetailNames()).not.toContain("Role Revoked Patient");
  expect(oldDelivery?.state.outcome).toBe("aborted");
  expect(
    screen.getByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeVisible();
  expect(viewCalls).toBe(1);
  expect(legacyCalls(fetchSpy)).toEqual([]);
});
