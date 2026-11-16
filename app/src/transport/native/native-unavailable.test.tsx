import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { ROUTES } from "../../app/routes";
import type { RoleCode } from "../../domain/capability";
import { CalendarPage } from "../../features/appointments/CalendarPage";
import { PharmacyPage } from "../../features/pharmacy/PharmacyPage";
import { LivePhotoWorkspace } from "../../features/photos/LivePhotoWorkspace";
import { QueuePage } from "../../features/reception/QueuePage";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";

function liveMessage(roles: RoleCode[]) {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles },
    capabilities: {},
    csrfToken: "native-csrf-token",
  };
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

const directLinks: Array<{
  name: string;
  role: RoleCode;
  ui: () => ReactNode;
}> = [
  { name: "appointments", role: "DOC", ui: () => <CalendarPage /> },
  { name: "lab photos", role: "DOC", ui: () => <LivePhotoWorkspace /> },
  { name: "pharmacy stock", role: "PHA", ui: () => <PharmacyPage /> },
  { name: "reception queue", role: "REC", ui: () => <QueuePage /> },
];

it.each(directLinks)(
  "presents $name as unavailable without touching the legacy proxy API",
  async ({ role, ui }) => {
    vi.stubEnv("VITE_CLINIC_NATIVE", "1");
    server.use(
      http.get(BOOTSTRAP, () =>
        HttpResponse.json({ message: liveMessage([role]) }),
      ),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    renderWithFixtures(ui(), { role, sessionMode: "live" });

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(legacyCalls(fetchSpy)).toEqual([]);
  },
);

it("never touches the legacy proxy API from a direct app link", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ message: liveMessage(["DOC"]) }),
    ),
  );
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  window.history.pushState({}, "", ROUTES.appointments);

  render(<App />);

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(legacyCalls(fetchSpy)).toEqual([]);
});
