import { renderHook, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionProvider, useSession } from "../../app/SessionProvider";
import { PatientDirectoryPage } from "../../features/patients/PatientDirectoryPage";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { postLogin, postLogout } from "../auth";
import { clearNativeCsrfToken } from "./session";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const DIRECTORY = "/api/method/clinic_ui.api.directory.synthetic_patients";
const LOGIN = "/api/method/login";
const LOGOUT = "/api/method/logout";
const CSRF_HEADER = "X-Frappe-CSRF-Token";
const TOKEN = "native-csrf-token";

function liveMessage() {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["DOC"] },
    capabilities: { "read.patient": "enabled" },
    csrfToken: TOKEN,
  };
}

function SessionWrapper({ children }: { children: ReactNode }) {
  return createElement(SessionProvider, { children });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  clearNativeCsrfToken();
});

describe("app wiring", () => {
  it("keeps the proxy transport when the build flag is off", async () => {
    const proxied = vi.fn();
    const native = vi.fn();
    server.use(
      http.get("/api/auth/csrf", () =>
        HttpResponse.json({ token: "proxy-csrf" }),
      ),
      http.post("/api/auth/login", () => {
        proxied();
        return HttpResponse.json({
          sessionEpoch: "proxy-epoch",
          user: { id: "u1", displayName: "Proxy", roles: ["REC"] },
          capabilities: {},
        });
      }),
      http.post(LOGIN, () => {
        native();
        return HttpResponse.json({ message: "Logged In" });
      }),
    );

    await postLogin("rec", "fixture");

    expect(proxied).toHaveBeenCalledTimes(1);
    expect(native).not.toHaveBeenCalled();
  });

  it("keeps proxy logout when the build flag is off", async () => {
    const native = vi.fn();
    server.use(
      http.get("/api/auth/csrf", () =>
        HttpResponse.json({ token: "proxy-csrf" }),
      ),
      http.post(
        "/api/auth/logout",
        () => new HttpResponse(null, { status: 204 }),
      ),
      http.post(LOGOUT, () => {
        native();
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    await postLogout();

    expect(native).not.toHaveBeenCalled();
  });

  it("routes login and logout through Frappe methods when the build flag is on", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const proxied = vi.fn();
    const csrfCalled = vi.fn();
    const logoutHeaders: Array<string | null> = [];
    server.use(
      http.get("/api/auth/csrf", () => {
        csrfCalled();
        return HttpResponse.json({ token: "proxy-csrf" });
      }),
      http.post("/api/auth/login", () => {
        proxied();
        return HttpResponse.json({});
      }),
      http.post(LOGIN, () => HttpResponse.json({ message: "Logged In" })),
      http.get(BOOTSTRAP, () => HttpResponse.json({ message: liveMessage() })),
      http.post(LOGOUT, ({ request }) => {
        logoutHeaders.push(request.headers.get(CSRF_HEADER));
        return HttpResponse.json({ message: "Logged Out" });
      }),
    );

    await postLogin("rec", "fixture");
    await postLogout();

    expect(csrfCalled).not.toHaveBeenCalled();
    expect(proxied).not.toHaveBeenCalled();
    expect(logoutHeaders).toEqual([TOKEN]);
  });

  it("reads the session from bootstrap when the build flag is on", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const proxySession = vi.fn();
    server.use(
      http.get("/api/session", () => {
        proxySession();
        return HttpResponse.json({}, { status: 500 });
      }),
      http.get(BOOTSTRAP, () => HttpResponse.json({ message: liveMessage() })),
    );

    const { result } = renderHook(() => useSession(), {
      wrapper: SessionWrapper,
    });

    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    expect(result.current.session?.mode).toBe("live");
    expect(result.current.session?.user.id).toBe("native|u1");
    expect(proxySession).not.toHaveBeenCalled();
  });

  it("reports a sign-in rejection in native mode without a proxy login", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    server.use(
      http.post(LOGIN, () =>
        HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
      ),
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
      ),
    );

    const { result } = renderHook(() => useSession(), {
      wrapper: SessionWrapper,
    });
    await waitFor(() => expect(result.current.status).toBe("anonymous"));
    await expect(postLogin("bad", "creds")).rejects.toEqual({
      code: "FORBIDDEN",
      retryable: false,
    });
  });

  it("reads the live directory from the Frappe method when the flag is on", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    const proxyDirectory = vi.fn();
    server.use(
      http.get(BOOTSTRAP, () => HttpResponse.json({ message: liveMessage() })),
      http.get(DIRECTORY, () =>
        HttpResponse.json({
          message: {
            items: [
              {
                workspace_patient_id: "workspace-1",
                native_patient_id: "native-1",
                display_name: "Native Directory Patient",
              },
            ],
          },
        }),
      ),
      http.get("*/api/live/synthetic-patients", () => {
        proxyDirectory();
        return HttpResponse.json({ items: [] });
      }),
    );

    renderWithFixtures(<PatientDirectoryPage />, { role: "DOC" });

    expect(await screen.findByText("Native Directory Patient")).toBeVisible();
    expect(proxyDirectory).not.toHaveBeenCalled();
  });

  it("runs the shared row validator on native directory rows", async () => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    server.use(
      http.get(BOOTSTRAP, () => HttpResponse.json({ message: liveMessage() })),
      http.get(DIRECTORY, () =>
        HttpResponse.json({ message: { items: [{ display_name: "Leaked" }] } }),
      ),
    );

    renderWithFixtures(<PatientDirectoryPage />, { role: "DOC" });

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("Leaked")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("keeps the proxy directory when the flag is off", async () => {
    const proxyDirectory = vi.fn();
    const nativeDirectory = vi.fn();
    server.use(
      http.get("*/api/live/synthetic-patients", () => {
        proxyDirectory();
        return HttpResponse.json({
          items: [
            {
              workspace_patient_id: "workspace-1",
              native_patient_id: "native-1",
              display_name: "Proxy Directory Patient",
            },
          ],
        });
      }),
      http.get(DIRECTORY, () => {
        nativeDirectory();
        return HttpResponse.json({ message: { items: [] } });
      }),
    );

    renderWithFixtures(<PatientDirectoryPage />, {
      role: "DOC",
      sessionMode: "live",
    });

    expect(await screen.findByText("Proxy Directory Patient")).toBeVisible();
    expect(nativeDirectory).not.toHaveBeenCalled();
  });
});
