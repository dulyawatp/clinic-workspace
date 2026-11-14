import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { ReceptionHomePage } from "../reception/ReceptionHomePage";
import { CalendarPage } from "./CalendarPage";

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
