import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { renderWithFixtures, setViewport } from "../../test/render";
import { LanguageToggle } from "../shell/LanguageToggle";
import { QueuePage } from "./QueuePage";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="queue-location">{location.search}</output>;
}

it("initializes the queue from an allowlisted status and removes private query parameters", async () => {
  renderWithFixtures(
    <>
      <QueuePage />
      <LocationProbe />
    </>,
    { path: "/clinic/reception/queue?status=waiting&q=patient-name" },
  );
  expect(await screen.findByText("Narin Chaiyaporn")).toBeVisible();
  expect(screen.queryByText("Daniel Okafor")).toBeNull();
  expect(screen.getByRole("combobox", { name: /สถานะ|status/i })).toHaveValue(
    "waiting",
  );
  await waitFor(() =>
    expect(screen.getByTestId("queue-location")).toHaveTextContent(
      "?status=waiting",
    ),
  );
  await userEvent.type(screen.getByRole("searchbox"), "Pim");
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: /สถานะ|status/i }),
    "arrived",
  );
  expect(screen.getByTestId("queue-location")).toHaveTextContent(
    "?status=arrived",
  );
  expect(screen.getByTestId("queue-location")).not.toHaveTextContent("Pim");
});

it("falls back to all rows for an unknown or identifying status value", async () => {
  renderWithFixtures(
    <>
      <QueuePage />
      <LocationProbe />
    </>,
    { path: "/clinic/reception/queue?status=patient-name" },
  );
  expect(await screen.findByText("Daniel Okafor")).toBeVisible();
  expect(screen.getByRole("combobox", { name: /สถานะ|status/i })).toHaveValue(
    "",
  );
  await waitFor(() =>
    expect(screen.getByTestId("queue-location")).toBeEmptyDOMElement(),
  );
});

it("offers disabled check-in only for the eligible appointment", async () => {
  renderWithFixtures(<QueuePage />);
  const buttons = await screen.findAllByRole("button", {
    name: /รับเข้าตรวจ|check in/i,
  });
  expect(buttons).toHaveLength(1);
  expect(buttons[0]).toBeDisabled();
});
it("offers no second check-in for an already checked-in row", async () => {
  renderWithFixtures(<QueuePage />, { rows: ["checkedIn"] });
  expect(await screen.findByText(/รับเข้าตรวจแล้ว|checked in/i)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /รับเข้าตรวจ|check in/i }),
  ).toBeNull();
});
it("annotates an unmapped source status", async () => {
  renderWithFixtures(<QueuePage />, { rows: ["weird"] });
  expect(await screen.findByText("marley_unknown_state")).toBeVisible();
  expect(screen.getByText(/ยังไม่ได้แมป|not mapped/i)).toBeVisible();
});
it("keeps the raw unmapped status and its annotation in English", async () => {
  renderWithFixtures(
    <>
      <LanguageToggle />
      <QueuePage />
    </>,
    { rows: ["weird"] },
  );
  expect(await screen.findByText("marley_unknown_state")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByText(/Unmapped source value/i)).toBeVisible();
  expect(screen.getByText("marley_unknown_state")).toBeVisible();
  expect(screen.queryByText(/ยังไม่ได้แมป/)).toBeNull();
});
it("labels fixture date and filters rows by server status", async () => {
  renderWithFixtures(<QueuePage />);
  expect(
    await screen.findByText(/วันที่ตัวอย่าง 2026-11-05|sample date 2026-11-05/i),
  ).toBeVisible();
  await userEvent.selectOptions(
    screen.getByLabelText(/สถานะ|status/i),
    "waiting",
  );
  expect(await screen.findByText("Narin Chaiyaporn")).toBeVisible();
  expect(screen.queryByText("Daniel Okafor")).toBeNull();
});

it("requests only the fixture snapshot date", async () => {
  renderWithFixtures(<QueuePage />, { rows: ["otherDay"] });
  expect(await screen.findByText("สมชาย ใจดี")).toBeVisible();
  expect(screen.queryByText("Other Day Patient")).toBeNull();
});

it("uses aggregate waiting button and server-side patient search", async () => {
  renderWithFixtures(<QueuePage />);
  const waiting = await screen.findByRole("button", {
    name: /รอตรวจ|waiting/i,
  });
  expect(waiting).toHaveTextContent("2");
  await userEvent.click(waiting);
  expect(await screen.findByText("Narin Chaiyaporn")).toBeVisible();
  expect(screen.queryByText("Daniel Okafor")).toBeNull();
  await userEvent.type(screen.getByRole("searchbox"), "Pimchanok");
  expect(await screen.findByText("Pimchanok Srisuk")).toBeVisible();
  await waitFor(() =>
    expect(screen.queryByText("Narin Chaiyaporn")).toBeNull(),
  );
});

it("never enables check-in when capability claims enabled", async () => {
  const spy = vi.spyOn(globalThis, "fetch");
  renderWithFixtures(<QueuePage />, {
    caps: {
      "write.appointment.checkin": "enabled",
      "write.appointment.book": "enabled",
    },
  });
  const button = await screen.findByRole("button", {
    name: /รับเข้าตรวจ|check in/i,
  });
  expect(button).toBeDisabled();
  await userEvent.click(button);
  expect(
    spy.mock.calls.every(([, init]) => !init?.method || init.method === "GET"),
  ).toBe(true);
  spy.mockRestore();
});

it("retains semantic column headers and explicit cell associations on mobile", async () => {
  setViewport(390);
  renderWithFixtures(<QueuePage />);
  const table = await screen.findByRole("table");
  const headers = within(table).getAllByRole("columnheader");
  expect(headers).toHaveLength(7);
  expect(
    headers.every((header) => header.getAttribute("scope") === "col"),
  ).toBe(true);
  const row = within(table).getByText("สมชาย ใจดี").closest("tr");
  expect(row).not.toBeNull();
  const cells = within(row as HTMLTableRowElement).getAllByRole("cell");
  expect(cells.map((cell) => cell.getAttribute("headers"))).toEqual(
    headers.map((header) => header.id),
  );
});
