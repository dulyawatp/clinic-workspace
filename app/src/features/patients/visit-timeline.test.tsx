import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { renderWithFixtures, setViewport } from "../../test/render";
import { PatientProfilePage } from "./PatientProfilePage";

afterEach(() => localStorage.removeItem(LOCALE_KEY));

it.each([1440, 390] as const)("shows a distinct dated sample history at %ipx", async (width) => {
  setViewport(width);
  localStorage.setItem(LOCALE_KEY, "en");
  renderWithFixtures(
    <Routes>
      <Route path={ROUTES.patientPattern} element={<PatientProfilePage />} />
    </Routes>,
    { path: `${ROUTES.patient("P-10241")}?view=history`, role: "DOC" },
  );
  const timeline = await screen.findByRole("list", { name: "Clinical timeline" });
  const visits = within(timeline).getAllByRole("article");
  expect(visits).toHaveLength(3);
  expect(visits[0]).toHaveAccessibleName("Visit DC-VIS-2041");
  expect(visits[1]).toHaveAccessibleName("Visit SAMPLE-HISTORY-02");
  expect(visits[2]).toHaveAccessibleName("Visit SAMPLE-HISTORY-01");
  expect(within(visits[0]!).getByText("result-77.pdf")).toBeVisible();
  expect(within(visits[0]!).getByText("Read visit note")).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "Visit narrative" })).not.toBeInTheDocument();
});
