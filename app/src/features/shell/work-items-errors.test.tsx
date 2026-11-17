import {
  focusManager,
  onlineManager,
  useQueryClient,
} from "@tanstack/react-query";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { HomeWorkItems } from "../home/HomeWorkItems";
import { LanguageToggle } from "./LanguageToggle";
import { NotificationsPanel } from "./NotificationsPanel";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
});

it.each([
  ["home", HomeWorkItems],
  ["notifications", NotificationsPanel],
] as const)(
  "%s explains unsupported native work items in both languages without retry",
  async (_, Panel) => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    server.use(
      http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
        HttpResponse.json({
          message: {
            mode: "live",
            sessionEpoch: "native-epoch-1",
            user: {
              id: "native|u1",
              displayName: "Native User",
              roles: ["REC"],
            },
            capabilities: {},
            csrfToken: "native-csrf-token",
          },
        }),
      ),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderWithFixtures(
      <>
        <LanguageToggle />
        <Panel />
      </>,
      { role: "REC", sessionMode: "live" },
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("รายการงานยังไม่พร้อมใช้งานในระบบนี้");
    expect(within(alert).queryByRole("button")).toBeNull();
    expect(screen.queryByText("ยังไม่อ่าน: 0")).toBeNull();
    if (Panel === HomeWorkItems)
      expect(screen.getByTestId("sample-banner")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(alert).toHaveTextContent(
      "Work items are not available in this system yet.",
    );
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        String(url).includes("/api/read/work-items"),
      ),
    ).toBe(false);
  },
);

it.each([
  ["home", HomeWorkItems],
  ["notifications", NotificationsPanel],
] as const)(
  "%s checks the session only when requested after 401",
  async (_, Panel) => {
    const reads = vi.fn();
    const sessionChecks = vi.fn();
    renderWithFixtures(<Panel />, { role: "REC" });
    server.use(
      http.get("/api/read/work-items", () => {
        reads();
        return HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 });
      }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("เซสชัน");
    server.use(
      http.get("/api/session", () => {
        sessionChecks();
        return new HttpResponse(null, { status: 401 });
      }),
    );
    expect(reads).toHaveBeenCalledTimes(1);
    expect(sessionChecks).not.toHaveBeenCalled();
    fireEvent.click(within(alert).getByRole("button", { name: "ลองใหม่" }));
    await vi.waitFor(() => expect(sessionChecks).toHaveBeenCalledTimes(1));
    expect(reads).toHaveBeenCalledTimes(1);
  },
);

function WorkItemsLifecycle() {
  const [shown, setShown] = useState(true);
  const client = useQueryClient();
  return (
    <>
      <button type="button" onClick={() => setShown(!shown)}>
        Toggle panel
      </button>
      <button
        type="button"
        onClick={() => void client.refetchQueries({ queryKey: ["work-items"] })}
      >
        Refresh
      </button>
      {shown && <NotificationsPanel />}
    </>
  );
}

it.each([
  [false, "focus"],
  [true, "focus"],
  [false, "reconnect"],
  [true, "reconnect"],
  [false, "remount"],
  [true, "remount"],
] as const)(
  "does not automatically retry errored work items (cached data: %s, trigger: %s)",
  async (cached, trigger) => {
    const requests = vi.fn();
    let fail = !cached;
    renderWithFixtures(<WorkItemsLifecycle />, { role: "REC" });
    server.use(
      http.get("/api/read/work-items", () => {
        requests();
        return fail
          ? HttpResponse.error()
          : HttpResponse.json({ items: [], total: 0 });
      }),
    );
    if (cached) {
      await screen.findByText("ไม่มีงานค้างสำหรับบทบาทนี้");
      fail = true;
      fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    }
    await screen.findByRole("alert");
    const initialCount = requests.mock.calls.length;
    expect(initialCount).toBe(cached ? 2 : 1);
    if (trigger === "focus") {
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
    } else if (trigger === "reconnect") {
      await act(async () => {
        onlineManager.setOnline(false);
        onlineManager.setOnline(true);
      });
    } else {
      fireEvent.click(screen.getByRole("button", { name: "Toggle panel" }));
      fireEvent.click(screen.getByRole("button", { name: "Toggle panel" }));
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(requests).toHaveBeenCalledTimes(initialCount);
    expect(screen.getByRole("alert")).toBeVisible();
  },
);

it.each([
  ["home", HomeWorkItems],
  ["notifications", NotificationsPanel],
] as const)(
  "%s reports non-retryable authorization errors without retry",
  async (_, Panel) => {
    renderWithFixtures(<Panel />, { role: "REC" });
    server.use(
      http.get("/api/read/work-items", () =>
        HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
      ),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("บัญชีนี้ไม่มีสิทธิ์ดำเนินการนี้");
    expect(within(alert).queryByRole("button")).toBeNull();
  },
);

it.each([
  ["home", HomeWorkItems],
  ["notifications", NotificationsPanel],
] as const)(
  "%s offers manual retry for a service failure",
  async (_, Panel) => {
    const requests = vi.fn();
    renderWithFixtures(<Panel />, { role: "REC" });
    server.use(
      http.get("/api/read/work-items", () => {
        requests();
        return HttpResponse.error();
      }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("ระบบปลายทางไม่พร้อมใช้งาน");
    expect(requests).toHaveBeenCalledTimes(1);
    fireEvent.click(within(alert).getByRole("button", { name: "ลองใหม่" }));
    await vi.waitFor(() => expect(requests).toHaveBeenCalledTimes(2));
  },
);
