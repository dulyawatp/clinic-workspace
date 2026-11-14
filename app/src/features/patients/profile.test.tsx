import frontdesk from "@clinic/contracts/fixtures/frontdesk.json";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import type { FixtureScenario } from "../../test/msw/handlers";
import { renderWithFixtures } from "../../test/render";
import { PatientProfilePage } from "./PatientProfilePage";

/** The route is the source of the record id, so the page renders behind one. */
const fixturePatientId = frontdesk.patients[0]?.id;
if (fixturePatientId === undefined)
  throw new Error("frontdesk fixture has no patient to profile");
const PROFILE_ID: string = fixturePatientId;

function renderProfile(scenario: FixtureScenario = {}) {
  return renderWithFixtures(
    <Routes>
      <Route path={`${ROUTES.patients}/:id`} element={<PatientProfilePage />} />
    </Routes>,
    { ...scenario, path: ROUTES.patient(PROFILE_ID) },
  );
}

it("shows measurement time and units for latest vitals", async () => {
  renderProfile({ patient: "withVitals" });
  expect(await screen.findByText(/38\.2\s*°C/)).toBeVisible();
  expect(screen.getByText(/03\/11\/2026|3 พ.ย./)).toBeVisible();
});

it("shows no-measurements copy instead of zeros", async () => {
  renderProfile({ patient: "noVitals" });
  expect(
    await screen.findByText(/ยังไม่มีการวัด|no measurements yet/i),
  ).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
});

it("hides payment timeline from a nurse", async () => {
  renderProfile({ role: "NUR" });
  expect(await screen.findByTestId("patient-id")).toBeVisible();
  expect(screen.queryByText(/payment|ชำระเงิน/i)).toBeNull();
});

it("keeps one patient identity in the header after tab change", async () => {
  const user = userEvent.setup();
  renderProfile();
  const identity = (await screen.findByTestId("patient-id")).textContent;
  await user.click(screen.getByRole("tab", { name: /photos|รูป/i }));
  expect(screen.getByTestId("patient-id")).toHaveTextContent(identity ?? "");
});

it("moves tab focus and selection with arrow keys", async () => {
  const user = userEvent.setup();
  renderProfile();
  const first = await screen.findByRole("tab", {
    name: /appointments|นัดหมาย/i,
  });
  first.focus();
  await user.keyboard("{ArrowRight}");

  const second = screen.getByRole("tab", { name: /photos|รูป/i });
  expect(second).toHaveFocus();
  expect(second).toHaveAttribute("aria-selected", "true");
  expect(first).toHaveAttribute("aria-selected", "false");
  expect(first).toHaveAttribute("tabindex", "-1");
});

/**
 * The identity-only billing record carries `{id, fullName, fictional}`, so the
 * profile shows the payments tab and nothing clinical: no birth date, masked
 * mobile, vitals or booking actions.
 */
async function expectBillingOnlyProfile(): Promise<void> {
  expect(await screen.findByTestId("patient-id")).toHaveTextContent(PROFILE_ID);
  expect(screen.getByRole("tab", { name: /payments|ชำระเงิน/i })).toBeVisible();
  expect(
    screen.queryByRole("tab", { name: /appointments|นัดหมาย/i }),
  ).toBeNull();
  expect(screen.queryByText(/birth date|วันเกิด/i)).toBeNull();
  expect(screen.queryByText("081-XXX-2233")).toBeNull();
  expect(screen.queryByText(/latest vitals|สัญญาณชีพล่าสุด/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /book|จองนัดหมาย/i })).toBeNull();
}

it("serves a cashier the identity-only billing profile", async () => {
  renderProfile({ role: "CAS" });
  await expectBillingOnlyProfile();
});

it("serves a manager the same identity-only billing profile", async () => {
  renderProfile({ role: "MGR" });
  await expectBillingOnlyProfile();
});

it("keeps the full clinical header for a receptionist", async () => {
  renderProfile();
  expect(await screen.findByTestId("patient-id")).toHaveTextContent(PROFILE_ID);
  expect(await screen.findByText(/birth date|วันเกิด/i)).toBeVisible();
  expect(screen.getByText("081-XXX-2233")).toBeVisible();
  expect(await screen.findByText(/latest vitals|สัญญาณชีพล่าสุด/i)).toBeVisible();
  expect(
    screen.getByRole("tab", { name: /appointments|นัดหมาย/i }),
  ).toBeVisible();
});
