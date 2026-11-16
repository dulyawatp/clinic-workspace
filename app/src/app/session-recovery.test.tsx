import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { SignInPage } from "../features/auth/SignInPage";
import { AppShell } from "../features/shell/AppShell";
import { LanguageToggle } from "../features/shell/LanguageToggle";
import { server } from "../test/msw/server";
import { renderWithFixtures } from "../test/render";
import { App } from "./App";
import { RequireSession } from "./guards";
import { RETURN_TO_KEY, rememberReturnTo } from "./return-to";
import { ROUTES } from "./routes";
import {
  SessionProvider,
  type SessionRefreshResult,
  useSession,
} from "./SessionProvider";

const session = {
  sessionEpoch: "test-session",
  user: { id: "fixture-rec", displayName: "Fixture", roles: ["REC"] },
  capabilities: {},
};

function Probe() {
  const { refresh, status } = useSession();
  return (
    <>
      <output data-testid="session-status">{status}</output>
      <button type="button" onClick={() => void refresh()}>
        Check elsewhere
      </button>
    </>
  );
}
function LoginRoutes() {
  return (
    <>
      <Probe />
      <Routes>
        <Route path={ROUTES.login} element={<SignInPage />} />
        <Route path={ROUTES.profile} element={<p>Returned safely</p>} />
      </Routes>
    </>
  );
}
afterEach(() => {
  sessionStorage.removeItem(RETURN_TO_KEY);
  vi.restoreAllMocks();
});

it("authenticated login route offers localized keyboard continuation and consumes safe target only after fresh confirmation", async () => {
  rememberReturnTo(`${ROUTES.profile}?view=day&name=Private`);
  const requests = vi.spyOn(globalThis, "fetch");
  renderWithFixtures(<LoginRoutes />, {
    role: "REC",
    path: `${ROUTES.login}?reason=expired`,
  });
  const proceed = await screen.findByRole("button", { name: "ไปยังพื้นที่ทำงาน" });
  expect(proceed).toHaveAccessibleDescription(
    "คุณเข้าสู่ระบบอยู่แล้ว ไปยังพื้นที่ทำงานได้โดยไม่ต้องเข้าสู่ระบบซ้ำ",
  );
  expect(screen.queryByRole("form")).toBeNull();
  expect(screen.queryByLabelText("รหัสผ่าน")).toBeNull();
  expect(screen.queryByText("เซสชันหมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง")).toBeNull();
  expect(screen.queryByText("Returned safely")).toBeNull();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(
    `${ROUTES.profile}?view=day`,
  );
  expect(requests.mock.calls.map(([url]) => url)).toEqual(["/api/session"]);

  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const confirmation = vi.fn();
  server.use(
    http.get("/api/session", async () => {
      confirmation();
      await pending;
      return HttpResponse.json(session);
    }),
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "EN" }));
  const englishProceed = screen.getByRole("button", {
    name: "Continue to workspace",
  });
  expect(englishProceed).toHaveAccessibleDescription(
    "You are already signed in. Continue to your workspace without signing in again.",
  );
  await user.tab();
  expect(englishProceed).toHaveFocus();
  await user.keyboard("{Enter}");
  await waitFor(() => expect(confirmation).toHaveBeenCalledOnce());
  expect(screen.getByTestId("session-status")).toHaveTextContent("loading");
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(
    `${ROUTES.profile}?view=day`,
  );
  expect(screen.queryByText("Returned safely")).toBeNull();
  await act(async () => {
    release();
  });
  expect(await screen.findByText("Returned safely")).toBeVisible();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  expect(requests.mock.calls.map(([url]) => url)).toEqual([
    "/api/session",
    "/api/session",
  ]);
  expect(
    requests.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
});

it.each(["error", "superseded success", "superseded failure"])(
  "authenticated continuation retains target on %s without writes",
  async (outcome) => {
    rememberReturnTo(ROUTES.profile);
    const requests = vi.spyOn(globalThis, "fetch");
    renderWithFixtures(<LoginRoutes />, { role: "REC", path: ROUTES.login });
    const proceed = await screen.findByRole("button", { name: "ไปยังพื้นที่ทำงาน" });
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let checks = 0;
    server.use(
      http.get("/api/session", async () => {
        if (++checks === 1) {
          await pending;
          return outcome === "superseded success"
            ? HttpResponse.json(session)
            : new HttpResponse(null, { status: 503 });
        }
        return HttpResponse.json(session);
      }),
    );
    fireEvent.click(proceed);
    await waitFor(() => expect(checks).toBe(1));
    if (outcome !== "error") {
      fireEvent.click(screen.getByRole("button", { name: "Check elsewhere" }));
      await screen.findByRole("button", { name: "ไปยังพื้นที่ทำงาน" });
    }
    await act(async () => {
      release();
    });
    if (outcome === "error") {
      await screen.findByRole("alert");
      expect(
        screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }),
      ).toBeVisible();
    } else {
      expect(
        screen.getByRole("button", { name: "ไปยังพื้นที่ทำงาน" }),
      ).toBeVisible();
      expect(screen.queryByRole("alert")).toBeNull();
    }
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByText("Returned safely")).toBeNull();
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.profile);
    expect(
      requests.mock.calls.every(
        ([url, init]) =>
          url === "/api/session" && (!init?.method || init.method === "GET"),
      ),
    ).toBe(true);
  },
);

it("resolved authenticated results lose freshness as soon as another check starts", async () => {
  const { result, unmount } = renderHook(() => useSession(), {
    wrapper: SessionProvider,
  });
  await waitFor(() => expect(result.current.status).toBe("anonymous"));
  server.use(http.get("/api/session", () => HttpResponse.json(session)));
  let previous!: SessionRefreshResult;
  await act(async () => {
    previous = await result.current.refresh();
  });
  expect(previous.status).toBe("authenticated");
  expect(previous.isCurrent()).toBe(true);
  await act(async () => {
    const next = result.current.refresh();
    expect(previous.isCurrent()).toBe(false);
    await next;
  });
  unmount();
  expect(previous.isCurrent()).toBe(false);
});

it("initial cookie check failure stays protected and recovers without login", async () => {
  window.history.pushState({}, "", ROUTES.home);
  let checks = 0;
  server.use(
    http.get("/api/session", () =>
      ++checks === 1 ? HttpResponse.error() : HttpResponse.json(session),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  render(<App />);
  await screen.findByRole("alert");
  expect(window.location.pathname).toBe(ROUTES.home);
  expect(screen.queryByTestId("role-home")).toBeNull();
  expect(screen.queryByLabelText("รหัสผ่าน")).toBeNull();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }));
  expect(await screen.findByTestId("role-home")).toBeVisible();
  expect(
    requests.mock.calls
      .filter(([url]) => url === "/api/session")
      .map(([url]) => url),
  ).toEqual(["/api/session", "/api/session"]);
  expect(
    requests.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
});

it.each(["network", "503"])(
  "recovers successful login after %s session failure with GET only",
  async (failure) => {
    renderWithFixtures(<LoginRoutes />, { authed: false, path: ROUTES.login });
    await waitFor(() =>
      expect(screen.getByTestId("session-status")).toHaveTextContent(
        "anonymous",
      ),
    );
    rememberReturnTo(ROUTES.profile);
    const requests = vi.spyOn(globalThis, "fetch");
    let checks = 0;
    server.use(
      http.get("/api/session", () =>
        ++checks === 1
          ? failure === "network"
            ? HttpResponse.error()
            : new HttpResponse(null, { status: 503 })
          : HttpResponse.json(session),
      ),
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("ชื่อผู้ใช้"), "rec");
    await user.type(screen.getByLabelText("รหัสผ่าน"), "fixture");
    await user.click(screen.getByRole("button", { name: "ดำเนินการต่อ" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ไม่สามารถตรวจสอบเซสชันได้",
    );
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.profile);
    expect(screen.queryByLabelText("รหัสผ่าน")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to check your session",
    );
    const beforeRetry = requests.mock.calls.length;
    await user.click(
      screen.getByRole("button", { name: "Retry session check" }),
    );
    expect(await screen.findByText("Returned safely")).toBeVisible();
    expect(requests.mock.calls.slice(beforeRetry).map(([url]) => url)).toEqual([
      "/api/session",
    ]);
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/auth/login"),
    ).toHaveLength(1);
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/auth/csrf"),
    ).toHaveLength(1);
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  },
);

it.each(["guard", "shell"])(
  "existing cookie survives transient error in %s without guest redirect",
  async (surface) => {
    renderWithFixtures(
      <>
        <Probe />
        <LanguageToggle />
        {surface === "guard" ? (
          <RequireSession>
            <p>Protected rows</p>
          </RequireSession>
        ) : (
          <AppShell>
            <p>Protected rows</p>
          </AppShell>
        )}
      </>,
      { role: "REC", path: ROUTES.home },
    );
    await screen.findByText("Protected rows");
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/session", async () => {
        await pending;
        return new HttpResponse(null, { status: 503 });
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Check elsewhere" }));
    expect(screen.queryByText("Protected rows")).toBeNull();
    await act(async () => {
      release();
    });
    await screen.findByRole("alert");
    expect(screen.queryByText("Protected rows")).toBeNull();
    expect(screen.queryByRole("link", { name: "เข้าสู่ระบบ" })).toBeNull();
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
    server.use(http.get("/api/session", () => HttpResponse.json(session)));
    fireEvent.click(screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }));
    expect(await screen.findByText("Protected rows")).toBeVisible();
  },
);

it("superseded successful refresh cannot navigate or consume target", async () => {
  renderWithFixtures(<LoginRoutes />, { authed: false, path: ROUTES.login });
  await waitFor(() =>
    expect(screen.getByTestId("session-status")).toHaveTextContent("anonymous"),
  );
  rememberReturnTo(ROUTES.profile);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let checks = 0;
  server.use(
    http.get("/api/session", async () => {
      if (++checks === 1) await pending;
      return HttpResponse.json(session);
    }),
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("ชื่อผู้ใช้"), "rec");
  await user.type(screen.getByLabelText("รหัสผ่าน"), "fixture");
  await user.click(screen.getByRole("button", { name: "ดำเนินการต่อ" }));
  await waitFor(() => expect(checks).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Check elsewhere" }));
  await waitFor(() =>
    expect(screen.getByTestId("session-status")).toHaveTextContent(
      "authenticated",
    ),
  );
  await act(async () => {
    release();
  });
  expect(screen.queryByText("Returned safely")).toBeNull();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.profile);
});

it.each([403, 500, 200])(
  "session response %s is error unless confirmed 401 or valid DTO",
  async (status) => {
    renderWithFixtures(<Probe />, { authed: false });
    await waitFor(() =>
      expect(screen.getByTestId("session-status")).toHaveTextContent(
        "anonymous",
      ),
    );
    server.use(
      http.get("/api/session", () =>
        HttpResponse.json({ malformed: true }, { status }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Check elsewhere" }));
    await waitFor(() =>
      expect(screen.getByTestId("session-status")).toHaveTextContent("error"),
    );
  },
);
