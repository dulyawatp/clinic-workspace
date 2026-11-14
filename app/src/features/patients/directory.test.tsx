import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { renderWithFixtures } from "../../test/render";
import { PatientDirectoryPage } from "./PatientDirectoryPage";
import { PatientProfilePage } from "./PatientProfilePage";

it("lists duplicate candidates separately", async () => {
  renderWithFixtures(<PatientDirectoryPage />, { duplicates: true });
  expect(await screen.findAllByText("Somchai Jaidee")).toHaveLength(2);
  expect(screen.getAllByText(/ผู้ป่วยซ้ำ|possible duplicate/i)).toHaveLength(2);
});

it("filters by Thai mobile digits", async () => {
  const user = userEvent.setup();
  renderWithFixtures(<PatientDirectoryPage />);
  await user.type(await screen.findByRole("searchbox"), "081");
  expect(await screen.findByText("สมชาย ใจดี")).toBeVisible();
  expect(screen.queryByText("Narin Chaiyaporn")).toBeNull();
});

it("shows pagination and total", async () => {
  renderWithFixtures(<PatientDirectoryPage />);
  expect(await screen.findByText(/ทั้งหมด|total/i)).toBeVisible();
});

it("offers register only as a disabled gated action", async () => {
  renderWithFixtures(<PatientDirectoryPage />);
  const btn = await screen.findByRole("button", {
    name: /ลงทะเบียน|register/i,
  });
  expect(btn).toBeDisabled();
});

it("opens the addressed patient record from a directory row", async () => {
  const user = userEvent.setup();
  renderWithFixtures(
    <Routes>
      <Route path={ROUTES.patients} element={<PatientDirectoryPage />} />
      <Route path={`${ROUTES.patients}/:id`} element={<PatientProfilePage />} />
    </Routes>,
    { path: ROUTES.patients },
  );

  const [open] = await screen.findAllByRole("link", {
    name: /เปิดระเบียน|open record/i,
  });
  const row = open?.closest("tr");
  const rowId = row?.querySelector(".patients-id")?.textContent ?? "";
  const rowName = row?.querySelector(".patients-name-text")?.textContent ?? "";
  expect(rowId).not.toBe("");
  expect(open).toHaveAttribute("href", ROUTES.patient(rowId));

  if (open !== undefined) await user.click(open);

  // One patient identity: the row the reader opened is the record rendered.
  expect(await screen.findByTestId("patient-id")).toHaveTextContent(rowId);
  expect(
    screen.getByRole("heading", { level: 2, name: rowName }),
  ).toBeVisible();
});
