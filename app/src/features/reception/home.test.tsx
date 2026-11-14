import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { renderWithFixtures, withFixtures } from "../../test/render";
import { ReceptionHomePage } from "./ReceptionHomePage";

it("shows four counts, each with a date scope and drilldown", async () => {
  renderWithFixtures(<ReceptionHomePage />);
  for (const key of ["todayAppointments", "checkedIn", "waiting", "upcoming"])
    expect(await screen.findByTestId(`kpi-${key}`)).toHaveTextContent(/\d/);
  expect(
    screen.getAllByRole("link", { name: /ดู|view/i }).length,
  ).toBeGreaterThanOrEqual(4);
});

it("links every KPI to the records behind its count", async () => {
  renderWithFixtures(<ReceptionHomePage />);
  expect(await screen.findByTestId("kpi-waiting")).toHaveAttribute(
    "href",
    `${ROUTES.queue}?status=waiting`,
  );
  for (const [key, scope] of [
    ["todayAppointments", "all"],
    ["checkedIn", "checkedIn"],
    ["upcoming", "upcoming"],
  ] as const)
    expect(await screen.findByTestId(`kpi-${key}`)).toHaveAttribute(
      "href",
      `${ROUTES.appointments}?scope=${scope}&date=2026-11-05`,
    );
});

it("opens checkout tile only when capability allows payment", async () => {
  renderWithFixtures(<ReceptionHomePage />, { caps: {} });
  expect(
    screen.queryByRole("link", { name: /ชำระเงิน|collect payment/i }),
  ).toBeNull();
});

it("shows an error instead of zeros when the aggregate fails", async () => {
  // `withFixtures` installs the scenario handlers before the page mounts, so
  // the failure override is registered last and actually wins.
  const page = withFixtures(<ReceptionHomePage />);
  server.use(http.get("/api/read/reception-kpis", () => HttpResponse.error()));
  render(page);
  expect((await screen.findAllByRole("alert"))[0]).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
});
