import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { renderWithFixtures, setViewport } from "../../test/render";
import { CalendarPage } from "./CalendarPage";

it.each([
  ["all", /all appointments|นัดหมายทั้งหมด/i],
  ["checkedIn", /checked-in appointments|นัดหมายที่รับเข้าแล้ว/i],
  ["waiting", /waiting appointments|นัดหมายที่รอพบแพทย์/i],
  ["upcoming", /upcoming appointments|นัดหมายที่ยังไม่มาถึง/i],
])("labels the %s drilldown scope from the catalogue", async (scope, label) => {
  renderWithFixtures(<CalendarPage />, {
    path: `${ROUTES.appointments}?scope=${scope}&date=2026-11-05`,
  });
  expect(await screen.findByText(label)).toBeVisible();
  expect(screen.queryByText(`rec.scope.${scope}`)).toBeNull();
});

it("renders day and week views", async () => {
  renderWithFixtures(<CalendarPage />);
  expect(await screen.findByRole("grid")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /สัปดาห์|week/i }));
  expect(screen.getByRole("grid")).toHaveAttribute("data-view", "week");
});

it("marks leave and break without treating them as available", async () => {
  renderWithFixtures(<CalendarPage />, { withLeave: true });
  expect(await screen.findByLabelText(/ลาพัก|leave/i)).toHaveAttribute(
    "data-state",
    "leave",
  );
});

it("opens a read-only slot without booking", async () => {
  renderWithFixtures(<CalendarPage />);
  await userEvent.click(await screen.findByRole("button", { name: /09:30/ }));
  expect(
    screen.queryByRole("button", { name: /ยืนยัน|confirm booking/i }),
  ).toBeNull();
});

it("shows seven days in week grid with keyboard navigation, and a chronological list", async () => {
  renderWithFixtures(<CalendarPage />);
  await screen.findByRole("grid");
  await userEvent.click(screen.getByRole("button", { name: /สัปดาห์|week/i }));
  const grid = screen.getByRole("grid");
  const cells = within(grid).getAllByRole("gridcell");
  expect(cells).toHaveLength(7);
  expect(
    within(cells[2] as HTMLElement).getByText(
      /ไม่มีตารางในวันนี้|no schedule on this day/i,
    ),
  ).toBeVisible();
  cells[0]?.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(cells[1]).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}{Enter}");
  expect(
    screen.getByRole("button", { name: /2026-11-05 Fixture Doctor 09:00/ }),
  ).toHaveFocus();
  await userEvent.keyboard("{ArrowDown}");
  expect(
    screen.getByRole("button", { name: /2026-11-05 Fixture Doctor 09:30/ }),
  ).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(cells[0]).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: /รายการ|list/i }));
  expect(screen.queryByRole("grid")).toBeNull();
  expect(
    screen.getByLabelText(/รายการนัดหมาย|appointment agenda/i),
  ).toHaveAttribute("data-view", "list");
  const agenda = screen.getByLabelText(/รายการนัดหมาย|appointment agenda/i);
  const times = within(agenda)
    .getAllByRole("button")
    .map((button) => button.getAttribute("aria-label"));
  expect(times[0]).toContain("09:00");
  expect(times[1]).toContain("09:30");
  expect(times.at(-1)).toContain("2026-11-06");
});

it("renders a phone agenda rather than a squeezed grid", async () => {
  setViewport(390);
  renderWithFixtures(<CalendarPage />);
  expect(
    await screen.findByLabelText(/รายการนัดหมาย|appointment agenda/i),
  ).toBeVisible();
  expect(screen.queryByRole("grid")).toBeNull();
});

it("filters date, doctor and source appointment type before showing slots", async () => {
  renderWithFixtures(<CalendarPage />);
  await screen.findByRole("grid");
  fireEvent.change(screen.getByLabelText(/ประเภทนัดหมาย|appointment type/i), {
    target: { value: "follow_up" },
  });
  expect(await screen.findByRole("button", { name: /09:30/ })).toBeVisible();
  expect(screen.queryByRole("button", { name: /11:00/ })).toBeNull();
  fireEvent.change(screen.getByLabelText(/^แพทย์$|^doctor$/i), {
    target: { value: "Fixture Practitioner" },
  });
  expect(
    await screen.findByText(/ไม่มีตารางในวันนี้|no schedule on this day/i),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText(/ประเภทนัดหมาย|appointment type/i), {
    target: { value: "" },
  });
  fireEvent.change(screen.getByLabelText(/วันที่|date/i), {
    target: { value: "2026-11-06" },
  });
  expect(
    await screen.findByRole("button", {
      name: /2026-11-06 Fixture Practitioner 10:00/,
    }),
  ).toBeVisible();
});

it("paginates more than 100 filtered rows without silently dropping them", async () => {
  const availability = Array.from({ length: 101 }, (_, i) => ({
    id: `bulk-${String(i).padStart(3, "0")}`,
    date: "2026-11-05",
    time: "09:00",
    practitionerId: "doctor",
    practitionerName: "Doctor",
    state: "booked",
    appointmentType: "consultation",
    fictional: true,
  }));
  const unrelated = Array.from({ length: 101 }, (_, i) => ({
    ...availability[0],
    id: `other-${i}`,
    date: "2026-10-01",
  }));
  renderWithFixtures(<CalendarPage />, {
    availability: [...unrelated, ...availability],
  });
  expect(await screen.findByText(/หน้า 1 จาก 2|page 1 of 2/i)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /ถัดไป|next/i }));
  expect(await screen.findByText(/หน้า 2 จาก 2|page 2 of 2/i)).toBeVisible();
  expect(
    screen.getAllByRole("button", { name: /2026-11-05 Doctor 09:00/ }),
  ).toHaveLength(1);
});

it("discovers later doctors and types, resolves names to exact ids, and rejects unresolved input", async () => {
  const spy = vi.spyOn(globalThis, "fetch");
  const availability = Array.from({ length: 101 }, (_, i) => ({
    id: `early-${i}`,
    date: "2026-11-05",
    time: "09:00",
    practitionerId: "early-id",
    practitionerName: "Early Doctor",
    state: "booked",
    appointmentType: "early-type",
    fictional: true,
  }));
  availability.push({
    id: "later-slot",
    date: "2026-11-05",
    time: "10:00",
    practitionerId: "later-id",
    practitionerName: "Later Doctor",
    state: "booked",
    appointmentType: "later-type",
    fictional: true,
  });
  renderWithFixtures(<CalendarPage />, { availability });
  await screen.findByRole("grid");
  const doctor = screen.getByLabelText(/^แพทย์$|^doctor$/i);
  expect(
    document.querySelector(
      'datalist#appointment-doctors option[value="Later Doctor"]',
    ),
  ).not.toBeNull();
  expect(screen.getByRole("option", { name: "later-type" })).toBeVisible();
  fireEvent.change(doctor, { target: { value: "Not a doctor" } });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /เลือกแพทย์|select a doctor/i,
  );
  expect(
    screen.queryByText(/ไม่มีตารางในวันนี้|no schedule on this day/i),
  ).toBeNull();
  fireEvent.change(doctor, { target: { value: "Later Doctor" } });
  fireEvent.change(screen.getByLabelText(/ประเภทนัดหมาย|appointment type/i), {
    target: { value: "later-type" },
  });
  expect(
    await screen.findByRole("button", { name: /Later Doctor 10:00/ }),
  ).toBeVisible();
  expect(
    spy.mock.calls.some(
      ([url]) =>
        typeof url === "string" &&
        url.includes("frontdesk-availability?") &&
        new URL(url, "http://localhost").searchParams
          .get("filters")
          ?.includes('"practitionerId":"later-id"'),
    ),
  ).toBe(true);
  spy.mockRestore();
});

it("disambiguates doctors with the same name by source id", async () => {
  const availability = ["one", "two"].map((id) => ({
    id,
    date: "2026-11-05",
    time: "09:00",
    practitionerId: id,
    practitionerName: "Same Name",
    state: "booked",
    fictional: true,
  }));
  renderWithFixtures(<CalendarPage />, { availability });
  await screen.findByRole("grid");
  const doctor = screen.getByLabelText(/^แพทย์$|^doctor$/i);
  fireEvent.change(doctor, { target: { value: "Same Name" } });
  expect(await screen.findByRole("alert")).toBeVisible();
  fireEvent.change(doctor, { target: { value: "Same Name — two" } });
  expect(
    await screen.findAllByRole("button", { name: /Same Name 09:00/ }),
  ).toHaveLength(1);
});
