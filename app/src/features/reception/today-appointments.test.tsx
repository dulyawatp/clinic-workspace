import frontdesk from "@clinic/contracts/fixtures/frontdesk.json";
import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { TodayAppointments } from "./TodayAppointments";

const eligible = frontdesk.appointments[0];
if (eligible === undefined)
  throw new Error("frontdesk fixture has no appointment row");

const CHECK_IN = /รับเข้าตรวจ|check in/i;

it("offers check-in for the one eligible preview row", async () => {
  renderWithFixtures(<TodayAppointments scope="all" />);
  expect(await screen.findByText(eligible.patientName)).toBeVisible();
  expect(screen.getAllByRole("button", { name: CHECK_IN })).toHaveLength(1);
});

it("offers no second check-in when the preview row is already checked in", async () => {
  renderWithFixtures(<TodayAppointments scope="all" />, {
    appointments: [
      {
        ...eligible,
        status: "arrived",
        checkedInAt: "2026-11-05T08:55:00+07:00",
      },
    ],
  });
  expect(await screen.findByText(eligible.patientName)).toBeVisible();
  expect(screen.queryByRole("button", { name: CHECK_IN })).toBeNull();
});
