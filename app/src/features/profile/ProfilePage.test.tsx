import { useQueryClient } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { createHandlers } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { Topbar } from "../shell/Topbar";
import { ProfilePage } from "./ProfilePage";

function RefreshProfile() {
  const client = useQueryClient();
  return (
    <button
      type="button"
      onClick={() => void client.refetchQueries({ queryKey: ["profile"] })}
    >
      Refresh profile
    </button>
  );
}
function ProfileRoutes() {
  return (
    <Routes>
      <Route path={ROUTES.profile} element={<ProfilePage />} />
      <Route path={ROUTES.login} element={<p>Signed out route</p>} />
    </Routes>
  );
}

function RefreshSession() {
  const { refresh, status } = useSession();
  return (
    <>
      <span data-testid="session-state">{status}</span>
      <button type="button" onClick={() => void refresh()}>
        Check session elsewhere
      </button>
    </>
  );
}

it("profile and topbar share read company and both hide it after refetch error", async () => {
  renderWithFixtures(
    <>
      <Topbar />
      <RefreshProfile />
      <ProfilePage />
    </>,
    { role: "REC", profileCompany: "Cached company" },
  );
  await waitFor(() =>
    expect(screen.getAllByText("Cached company")).toHaveLength(2),
  );
  server.use(http.get("/api/read/profile", () => HttpResponse.error()));
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Cached company")).toBeNull();
  expect(screen.getByTestId("shell-company")).toHaveTextContent("ไม่พร้อมใช้งาน");
});

it("logout POST failure is safe and never automatically retried", async () => {
  renderWithFixtures(<ProfilePage />, { role: "REC" });
  await screen.findByRole("button", { name: "ออกจากระบบ" });
  let writes = 0;
  server.use(
    http.post("/api/auth/logout", () => {
      writes += 1;
      return HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      );
    }),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  try {
    fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
    // The failed logout never claims sign-out: the reconciliation GET restores
    // the session that still exists, and no upstream text is exposed.
    expect(
      await screen.findByRole("button", { name: "ออกจากระบบ" }),
    ).toBeEnabled();
    expect(screen.getByTestId("profile-name")).toBeVisible();
    expect(screen.queryByText("private trace")).toBeNull();
    expect(writes).toBe(1);
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
    ).toHaveLength(1);
    // One reconciliation GET after the failed POST; the spy starts after mount.
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/session"),
    ).toHaveLength(1);
  } finally {
    requests.mockRestore();
  }
});

it("superseded logout session confirmation never navigates over newer authenticated session", async () => {
  renderWithFixtures(
    <>
      <RefreshSession />
      <ProfileRoutes />
    </>,
    { role: "REC", path: ROUTES.profile },
  );
  await screen.findByRole("button", { name: "ออกจากระบบ" });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let checks = 0;
  server.use(
    http.get("/api/session", async () => {
      if (++checks === 1) {
        await pending;
        return new HttpResponse(null, { status: 401 });
      }
      return HttpResponse.json({
        sessionEpoch: "new-session",
        user: { id: "new-user", displayName: "New staff", roles: ["DOC"] },
        capabilities: {},
      });
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
  await waitFor(() => expect(checks).toBe(1));
  fireEvent.click(
    screen.getByRole("button", { name: "Check session elsewhere" }),
  );
  await screen.findByText("New staff");
  await act(async () => {
    release();
  });
  expect(screen.queryByText("Signed out route")).toBeNull();
  expect(screen.getByTestId("session-state")).toHaveTextContent(
    "authenticated",
  );
});

it("shows verified session identity, company and device-scoped locale without role switch", async () => {
  renderWithFixtures(<ProfilePage />, {
    role: "REC",
    profileCompany: "Fixture clinic",
  });
  expect(await screen.findByText("Fixture clinic")).toBeVisible();
  expect(screen.getByTestId("profile-name")).not.toBeEmptyDOMElement();
  expect(screen.getByText(/การเปลี่ยนภาษามีผลกับเบราว์เซอร์นี้/)).toBeVisible();
  const storage = vi.spyOn(Storage.prototype, "setItem");
  try {
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(
      screen.getByRole("heading", { name: "Profile & language" }),
    ).toBeVisible();
    expect(screen.getByText(/not saved to your profile/)).toBeVisible();
    // The choice is remembered on this device only, never on the profile.
    expect(storage).toHaveBeenCalledTimes(1);
    expect(storage).toHaveBeenCalledWith("clinic.locale", "en");
    expect(screen.queryByRole("button", { name: /switch role/i })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  } finally {
    storage.mockRestore();
  }
});

it("company becomes unavailable after read failure, never stale cached company", async () => {
  renderWithFixtures(
    <>
      <RefreshProfile />
      <ProfilePage />
    </>,
    { role: "REC", profileCompany: "Old company" },
  );
  await screen.findByText("Old company");
  server.use(
    http.get(
      "/api/read/profile",
      () => new HttpResponse(null, { status: 503 }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Old company")).toBeNull();
  expect(screen.getByText("ไม่พร้อมใช้งาน")).toBeVisible();
});

it("does not invent missing company", async () => {
  renderWithFixtures(<ProfilePage />, { role: "REC", profileCompany: null });
  await screen.findByTestId("profile-name");
  expect(screen.getByText("ไม่พร้อมใช้งาน")).toBeVisible();
});

it("logs out with fresh CSRF then confirmed anonymous navigation, no role switch", async () => {
  renderWithFixtures(<ProfileRoutes />, { role: "REC", path: ROUTES.profile });
  const button = await screen.findByRole("button", { name: "ออกจากระบบ" });
  const requests = vi.spyOn(globalThis, "fetch");
  try {
    await userEvent.setup().click(button);
    expect(await screen.findByText("Signed out route")).toBeVisible();
    expect(
      requests.mock.calls
        .filter(([url]) => String(url).startsWith("/api/auth"))
        .map(([url]) => url),
    ).toEqual(["/api/auth/csrf", "/api/auth/logout"]);
    expect(requests.mock.calls.some(([url]) => url === "/api/session")).toBe(
      true,
    );
  } finally {
    requests.mockRestore();
  }
});

it("logout success then failed session check recovers with GET only, never repeats logout", async () => {
  renderWithFixtures(<ProfileRoutes />, { role: "REC", path: ROUTES.profile });
  await screen.findByRole("button", { name: "ออกจากระบบ" });
  let checks = 0;
  server.use(
    http.get("/api/session", () =>
      ++checks === 1
        ? HttpResponse.error()
        : new HttpResponse(null, { status: 401 }),
    ),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  try {
    fireEvent.click(screen.getByRole("button", { name: "ออกจากระบบ" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("profile-name")).toBeNull();
    const before = requests.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }));
    expect(await screen.findByText("Signed out route")).toBeVisible();
    expect(requests.mock.calls.slice(before).map(([url]) => url)).toEqual([
      "/api/session",
    ]);
    expect(
      requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
    ).toHaveLength(1);
  } finally {
    requests.mockRestore();
  }
});

it("mounts profile in protected real router", async () => {
  server.use(...createHandlers({ role: "REC" }));
  window.history.pushState({}, "", ROUTES.profile);
  render(<App />);
  expect(await screen.findByTestId("profile-name")).toBeVisible();
  expect(screen.getByRole("heading", { name: "โปรไฟล์และภาษา" })).toBeVisible();
});
