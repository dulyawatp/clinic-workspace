import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { ReceptionHomePage } from "../reception/ReceptionHomePage";
import { CalendarPage } from "./CalendarPage";

const NATIVE_BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const NATIVE_APPOINTMENTS =
  "/api/method/clinic_ui.api.appointments.synthetic_appointments";

afterEach(() => {
  vi.unstubAllEnvs();
});

const row = {
  workspace_patient_id: "patient-ref",
  appointment_id: "appt-ref",
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Open",
};

it.each([CalendarPage, ReceptionHomePage])(
  "shows live records without fixture availability or actions",
  async (Page) => {
    server.use(
      http.get("*/api/live/synthetic-appointments", () =>
        HttpResponse.json({ items: [row] }),
      ),
    );
    renderWithFixtures(<Page />, { role: "REC", sessionMode: "live" });
    const table = await screen.findByRole("table");
    for (const value of Object.values(row))
      expect(within(table).getByText(value)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /จอง|เช็กอิน|book|check.in/i }),
    ).toBeNull();
  },
);

it("shows empty state", async () => {
  server.use(
    http.get("*/api/live/synthetic-appointments", () =>
      HttpResponse.json({ items: [] }),
    ),
  );
  renderWithFixtures(<CalendarPage />, { role: "DOC", sessionMode: "live" });
  expect(await screen.findByText("ไม่มีรายการนัดหมายที่แสดงได้")).toBeVisible();
});

it.each([
  { ...row, appointment_date: "2026-02-30" },
  { ...row, appointment_time: "25:00" },
  { ...row, status: "<bad>" },
])("rejects malformed response", async (invalid) => {
  server.use(
    http.get("*/api/live/synthetic-appointments", () =>
      HttpResponse.json({ items: [row, invalid] }),
    ),
  );
  renderWithFixtures(<CalendarPage />, { role: "REC", sessionMode: "live" });
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("patient-ref")).toBeNull();
});

function nativeSession() {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["REC"] },
    capabilities: { "read.appointment": "read_only" },
    csrfToken: "native-csrf-token",
  };
}

it("reads the native appointment method with no proxy fallback", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const proxy = vi.fn();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      HttpResponse.json({ message: nativeSession() }),
    ),
    http.get(NATIVE_APPOINTMENTS, () =>
      HttpResponse.json({ message: { items: [row] } }),
    ),
    http.get("*/api/live/synthetic-appointments", () => {
      proxy();
      return HttpResponse.json({ items: [] });
    }),
  );

  renderWithFixtures(<CalendarPage />, { role: "REC", sessionMode: "live" });

  const table = await screen.findByRole("table");
  for (const value of Object.values(row))
    expect(within(table).getByText(value)).toBeVisible();
  expect(proxy).not.toHaveBeenCalled();
});

it("reconciles an expired session behind a pre-whitelist 403", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  let appointmentRead = false;
  const proxy = vi.fn();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      appointmentRead
        ? HttpResponse.json({}, { status: 401 })
        : HttpResponse.json({ message: nativeSession() }),
    ),
    http.get(NATIVE_APPOINTMENTS, () => {
      appointmentRead = true;
      return HttpResponse.json({}, { status: 403 });
    }),
    http.get("*/api/live/synthetic-appointments", () => {
      proxy();
      return HttpResponse.json({ items: [] });
    }),
  );

  renderWithFixtures(<CalendarPage />, { role: "REC", sessionMode: "live" });

  expect(
    await screen.findByRole("button", { name: /ลองใหม่|retry/i }),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
  ).toBeNull();
  expect(proxy).not.toHaveBeenCalled();
});

it("rejects a row with an unexpected native identifier field", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      HttpResponse.json({ message: nativeSession() }),
    ),
    http.get(NATIVE_APPOINTMENTS, () =>
      HttpResponse.json({
        message: { items: [{ ...row, native_patient_id: "native-1" }] },
      }),
    ),
  );

  renderWithFixtures(<CalendarPage />, { role: "REC", sessionMode: "live" });

  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("native-1")).toBeNull();
  expect(screen.queryByRole("table")).toBeNull();
});

it("accepts native identifier values in the canonical fields", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      HttpResponse.json({ message: nativeSession() }),
    ),
    http.get(NATIVE_APPOINTMENTS, () =>
      HttpResponse.json({
        message: {
          items: [
            {
              workspace_patient_id: "patient-native",
              appointment_id: "appt-native",
              appointment_date: "2026-11-20",
              appointment_time: "09:30:00",
              status: "Open",
            },
          ],
        },
      }),
    ),
  );

  renderWithFixtures(<CalendarPage />, { role: "REC", sessionMode: "live" });

  const table = await screen.findByRole("table");
  for (const value of [
    "appt-native",
    "patient-native",
    "2026-11-20",
    "09:30:00",
    "Open",
  ])
    expect(within(table).getByText(value)).toBeVisible();
});
