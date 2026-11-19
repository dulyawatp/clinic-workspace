import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { PatientProfilePage } from "../patients/PatientProfilePage";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { http } from "msw";
import { EncounterLayout } from "./ApprovedVisitWorkspace";
import { VisitWorkspacePage } from "./VisitWorkspacePage";

afterEach(() => {
  vi.unstubAllEnvs();
  localStorage.removeItem(LOCALE_KEY);
});

function renderVisit(path = ROUTES.visit("DC-VIS-2041"), sessionMode?: "live") {
  localStorage.setItem(LOCALE_KEY, "en");
  renderWithFixtures(
    <>
      <Link to={ROUTES.visit("DC-VIS-2042")}>Switch visit</Link>
      <Routes>
        <Route path={ROUTES.visitPattern} element={<VisitWorkspacePage />} />
        <Route path={ROUTES.patientPattern} element={<PatientProfilePage />} />
      </Routes>
    </>,
    { path, role: "DOC", ...(sessionMode && { sessionMode }) },
  );
}

it.each([1440, 390] as const)("keeps approved document order at %ipx", async (width) => {
  setViewport(width);
  renderVisit();
  const panel = await screen.findByTestId("sample-visit-workspace");
  const headings = within(panel).getAllByRole("heading").map((node) => node.textContent);
  expect(headings.filter((title) => [
    "Encounter details", "Clinical Note", "Medications", "Same-visit photos", "Lab Results",
  ].includes(title ?? ""))).toEqual([
    "Encounter details", "Clinical Note", "Medications", "Same-visit photos", "Lab Results",
  ]);
  expect(screen.getByText("More details").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByText("Vital Signs")).not.toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
});

it("keeps the clinical note save separate from the draft and resets on switch", async () => {
  const user = userEvent.setup();
  renderVisit();
  const note = await screen.findByRole("textbox", { name: "Visit narrative" });
  await user.clear(note);
  await user.type(note, "Note for this visit only");
  await user.click(screen.getByRole("button", { name: "Save Clinical Note" }));
  expect(screen.getByText("Sample note saved for this visit.")).toBeVisible();
  expect(screen.getByTestId("visit-save-status")).toHaveTextContent(/not saved/i);
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await user.type(note, " (edited)");
  expect(screen.getByText("Note has unsaved changes.")).toBeVisible();
  await user.click(screen.getByRole("link", { name: "Switch visit" }));
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("DC-VIS-2042");
  expect(screen.getByRole("textbox", { name: "Visit narrative" })).not.toHaveValue(
    "Note for this visit only (edited)",
  );
  expect(screen.queryByText("Sample note saved for this visit.")).not.toBeInTheDocument();
});

it("does not resolve a sample encounter in a live native session", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC", {})));
  renderVisit(ROUTES.visit("DC-VIS-2041"), "live");
  expect(await screen.findByText("Visit not found")).toBeVisible();
  expect(screen.queryByTestId("sample-visit-workspace")).not.toBeInTheDocument();
});

it("shows unconnected native sections without rendering sample controls or records", () => {
  localStorage.setItem(LOCALE_KEY, "en");
  renderWithFixtures(<EncounterLayout status="Draft" details={<p>Verified draft fields</p>} />, { role: "DOC" });
  expect(screen.getByText("Clinical Note saving is not connected here.")).toBeVisible();
  expect(screen.getByText("Same-visit photo status is not available here.")).toBeVisible();
  expect(screen.getByText("Private lab reports are not available here.")).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "Visit narrative" })).not.toBeInTheDocument();
  expect(screen.queryByText("result-77.pdf")).not.toBeInTheDocument();
});

it("shows a report and matched comparison in their own visit without writing", async () => {
  const restoreDialog = installDialogMethods();
  const network = postSpy();
  const user = userEvent.setup();
  try {
    renderVisit();
    const photos = await screen.findByRole("region", { name: "Same-visit photos" });
    expect(within(photos).queryByRole("button", { name: "Compare before & after" })).not.toBeInTheDocument();
    await user.click(within(photos).getByRole("button", { name: "Add sample After" }));
    expect(within(photos).getByRole("button", { name: "Compare before & after" })).toBeVisible();
    expect(within(photos).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      ROUTES.photos("P-10241"), ROUTES.photoCompare("P-10241"),
    ]);
    await user.click(screen.getByRole("button", { name: "Attach sample report" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Attach sample report" }));
    await user.click(screen.getByRole("link", { name: "Visit history" }));
    const timeline = await screen.findByRole("list", { name: "Clinical timeline" });
    const current = within(timeline).getByRole("article", { name: "Visit DC-VIS-2041" });
    const older = within(timeline).getByRole("article", { name: "Visit SAMPLE-HISTORY-02" });
    expect(within(current).getByRole("button", { name: "sample-lab-report.pdf" })).toBeVisible();
    expect(within(current).getByRole("button", { name: "Compare before & after" })).toBeVisible();
    expect(within(older).queryByRole("button", { name: "sample-lab-report.pdf" })).not.toBeInTheDocument();
    expect(network.posts).toEqual([]);
  } finally {
    restoreDialog();
    network.stop();
  }
});

it("reviews multiple picked symptoms and optional comment without mixing in the note", async () => {
  const restoreDialog = installDialogMethods();
  const user = userEvent.setup();
  try {
    renderVisit();
    const symptoms = await screen.findByRole("searchbox", { name: "Symptoms" });
    for (const term of ["Itching", "Redness"]) {
      await user.type(symptoms, term);
      await user.click(screen.getByRole("button", { name: term }));
    }
    await user.click(screen.getByText("More details"));
    await user.type(screen.getByRole("textbox", { name: "Encounter comment (optional)" }), "Draft comment only");
    await user.clear(screen.getByRole("textbox", { name: "Visit narrative" }));
    await user.type(screen.getByRole("textbox", { name: "Visit narrative" }), "Separate narrative");
    await user.click(screen.getByRole("button", { name: "Review encounter" }));
    const review = screen.getByRole("dialog");
    expect(review).toHaveTextContent("Itching · Redness");
    expect(review).toHaveTextContent("Draft comment only");
    expect(review).not.toHaveTextContent("Separate narrative");
  } finally {
    restoreDialog();
  }
});
