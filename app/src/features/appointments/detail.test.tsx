import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { LanguageToggle } from "../shell/LanguageToggle";
import { AppointmentPage } from "./AppointmentPage";

function renderDetail(status?: string) {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: "/clinic/appointments/fixture-apt-01", status },
  );
}
it("shows booking fields as read-only with a reason", async () => {
  renderDetail();
  expect(await screen.findByLabelText(/วันที่|date/i)).toBeDisabled();
  expect(
    screen.getAllByText(/รอการตรวจสอบ|awaiting backend verification/i).length,
  ).toBeGreaterThan(0);
});
it("does not render a cancel control for a completed appointment", async () => {
  renderDetail("completed");
  await screen.findByLabelText(/วันที่|date/i);
  expect(screen.queryByRole("button", { name: /ยกเลิก|cancel/i })).toBeNull();
});
it("shows the server status, not a local status", async () => {
  renderDetail("no_show");
  expect(await screen.findByDisplayValue("ไม่มา")).toBeVisible();
});

it("labels a mapped appointment status in English", async () => {
  renderWithFixtures(
    <>
      <LanguageToggle />
      <Routes>
        <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
      </Routes>
    </>,
    { path: "/clinic/appointments/fixture-apt-01", status: "no_show" },
  );
  await screen.findByLabelText(/วันที่|date/i);
  await userEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByDisplayValue("No-show")).toBeVisible();
  expect(screen.queryByDisplayValue("ไม่มา")).toBeNull();
});

it("keeps write controls disabled even with an enabled capability", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    {
      path: "/clinic/appointments/fixture-apt-01",
      caps: { "write.appointment.book": "enabled" },
    },
  );
  expect(
    await screen.findByRole("button", { name: /จองนัด|book/i }),
  ).toBeDisabled();
  for (const name of [
    /จองนัด|book/i,
    /เลื่อนนัด|reschedule/i,
    /ยกเลิกนัด|cancel/i,
    /ระบุว่าไม่มา|mark no-show/i,
  ]) {
    const control = screen.getByRole("button", { name });
    expect(control).toBeDisabled();
    await userEvent.click(control);
  }
  expect(
    fetchSpy.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
  fetchSpy.mockRestore();
});

it("identifies the patient by source id and labels missing fields", async () => {
  renderDetail();
  expect(await screen.findByText("fixture-dir-01")).toBeVisible();
  for (const label of [
    /ห้อง|room/i,
    /บริษัท|company/i,
    /เหตุผล|reason/i,
    /หมายเหตุ|notes/i,
  ]) {
    expect(screen.getByLabelText(label)).toHaveValue("ไม่มีข้อมูลจากต้นทาง");
  }
});

it("does not substitute another record for an unknown id", async () => {
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: "/clinic/appointments/not-a-real-id" },
  );
  expect(
    await screen.findByText(/ไม่พบนัดหมาย|appointment not found/i),
  ).toBeVisible();
  expect(screen.queryByText("fixture-dir-01")).toBeNull();
});

it("does not offer cancellation of completed appointments even when enabled", async () => {
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    {
      path: "/clinic/appointments/fixture-apt-01",
      status: "completed",
      caps: { "write.appointment.book": "enabled" },
    },
  );
  await screen.findByLabelText(/วันที่|date/i);
  expect(screen.queryByRole("button", { name: /ยกเลิก|cancel/i })).toBeNull();
});
