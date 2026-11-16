import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { useLocale } from "../../i18n/LocaleProvider";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { SignInPage } from "./SignInPage";

function Destination() {
  const location = useLocation();
  const { locale } = useLocale();
  const { status } = useSession();
  return (
    <div data-testid="destination">
      {location.pathname}
      {location.search} {locale} {status}
    </div>
  );
}

function SignInRoutes() {
  return (
    <Routes>
      <Route path={ROUTES.login} element={<SignInPage />} />
      <Route path={ROUTES.home} element={<Destination />} />
      <Route path={ROUTES.profile} element={<Destination />} />
    </Routes>
  );
}

async function fillAndSubmit(password = "fixture") {
  const user = userEvent.setup();
  await waitFor(() =>
    expect(screen.getByLabelText(/ชื่อผู้ใช้|username/i)).toBeEnabled(),
  );
  await user.type(screen.getByLabelText(/ชื่อผู้ใช้|username/i), "rec");
  await user.type(screen.getByLabelText(/รหัสผ่าน|password/i), password);
  await user.click(
    screen.getByRole("button", { name: /continue|ดำเนินการต่อ/i }),
  );
}

describe("SignInPage", () => {
  it("starts Thai, exposes EN, labels credentials and never renders fixture account details", () => {
    renderWithFixtures(<SignInPage />, { authed: false, path: ROUTES.login });
    expect(screen.getByRole("button", { name: "EN" })).toBeVisible();
    expect(screen.getByLabelText("ชื่อผู้ใช้")).toHaveAttribute(
      "autocomplete",
      "username",
    );
    expect(screen.getByLabelText("รหัสผ่าน")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("รหัสผ่าน")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    expect(screen.getByLabelText("ชื่อผู้ใช้")).toHaveValue("");
    expect(screen.getByLabelText("รหัสผ่าน")).toHaveValue("");
    expect(screen.queryByText(/fixture|rec@example|rec\/fixture/i)).toBeNull();
    expect(screen.getAllByRole("link")[0]).toHaveAttribute(
      "href",
      "#signin-main",
    );
  });

  it("shows a localized safe error and clears password after invalid credentials", async () => {
    renderWithFixtures(<SignInPage />, { authed: false, path: ROUTES.login });
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { code: "FORBIDDEN", message: "private traceback" },
          { status: 403 },
        ),
      ),
    );
    await fillAndSubmit("nope");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/ไม่มีสิทธิ์/);
    expect(alert).toHaveFocus();
    expect(screen.getByLabelText("รหัสผ่าน")).toHaveValue("");
    expect(screen.queryByText(/private traceback/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(alert).toHaveTextContent(/permission/i);
  });

  it("shows localized network failure without transport text", async () => {
    renderWithFixtures(<SignInPage />, { authed: false, path: ROUTES.login });
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    server.use(http.get("/api/auth/csrf", () => HttpResponse.error()));
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service is unavailable. Try again.",
    );
  });

  it("shows expired-session banner in either locale without promising draft retention", () => {
    renderWithFixtures(<SignInPage />, {
      authed: false,
      path: `${ROUTES.login}?reason=expired`,
    });
    expect(screen.getByRole("status")).toHaveTextContent(/เซสชันหมดอายุ/);
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your session expired. Sign in again.",
    );
  });

  it("refreshes authenticated session and preserves chosen locale through safe return navigation", async () => {
    renderWithFixtures(<SignInRoutes />, {
      authed: false,
      path: `${ROUTES.login}?returnTo=${encodeURIComponent(`${ROUTES.profile}?view=day&patient=private`)}`,
    });
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    const storage = vi.spyOn(Storage.prototype, "setItem");
    try {
      await fillAndSubmit();
      expect(await screen.findByTestId("destination")).toHaveTextContent(
        `${ROUTES.profile}?view=day en authenticated`,
      );
      expect(storage).not.toHaveBeenCalled();
    } finally {
      storage.mockRestore();
    }
  });

  it("falls back home instead of following an external return target", async () => {
    renderWithFixtures(<SignInRoutes />, {
      authed: false,
      path: `${ROUTES.login}?returnTo=${encodeURIComponent("https://evil.test")}`,
    });
    await fillAndSubmit();
    expect(await screen.findByTestId("destination")).toHaveTextContent(
      `${ROUTES.home} th authenticated`,
    );
  });

  it("retains in-memory form fields when language changes, without persistence", async () => {
    renderWithFixtures(<SignInPage />, { authed: false, path: ROUTES.login });
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("ชื่อผู้ใช้")).toBeEnabled());
    await user.type(screen.getByLabelText("ชื่อผู้ใช้"), "staff-name");
    await user.type(screen.getByLabelText("รหัสผ่าน"), "transient-secret");
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByLabelText("Username")).toHaveValue("staff-name");
    expect(screen.getByLabelText("Password")).toHaveValue("transient-secret");
  });

  it("mounts actual login route and keeps chosen locale when real router opens authenticated home", async () => {
    window.history.pushState({}, "", ROUTES.login);
    render(<App />);
    expect(
      screen.getByRole("heading", { level: 1, name: "เข้าสู่ระบบ" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    await fillAndSubmit();
    expect(await screen.findByTestId("role-home")).toBeVisible();
    expect(window.location.pathname).toBe(ROUTES.home);
    expect(document.documentElement.lang).toBe("en");
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("prevents duplicate submissions and clears password while request is pending", async () => {
    renderWithFixtures(<SignInPage />, { authed: false, path: ROUTES.login });
    let release!: () => void;
    const stalled = new Promise<void>((resolve) => {
      release = resolve;
    });
    const login = vi.fn();
    server.use(
      http.post("/api/auth/login", async () => {
        login();
        await stalled;
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      }),
    );
    await fillAndSubmit();
    await waitFor(() => expect(login).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("รหัสผ่าน")).toHaveValue("");
    expect(screen.getByRole("button", { name: "กำลังโหลด…" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("form"));
    expect(login).toHaveBeenCalledOnce();
    release();
    await screen.findByRole("alert");
  });
});
