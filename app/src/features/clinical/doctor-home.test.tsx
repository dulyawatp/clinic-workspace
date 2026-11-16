import { screen, within } from "@testing-library/react";
import { http } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { coreData } from "../../fixtures/data";
import { opsData } from "../../fixtures/data-ops";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import { server } from "../../test/msw/server";
import {
  NATIVE_BOOTSTRAP,
  nativeSession,
  postSpy,
} from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { DoctorHomePage } from "./DoctorHomePage";
import { LabWorkspacePage } from "./LabWorkspacePage";
import { followUpVisitsFor, labRequests, patientLabel } from "./sample-scope";

function renderDoctorHome(path = ROUTES.doctor) {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.doctor} element={<DoctorHomePage />} />
      <Route path={ROUTES.labPattern} element={<LabWorkspacePage />} />
    </Routes>,
    { role: "DOC", path },
  );
}

const PRACTITIONER = "HP-01";

function expectedCounts() {
  const today = coreData.dates.todayKey();
  return {
    appointmentsToday: coreData.appointments.filter(
      (appointment) =>
        appointment.practitioner === PRACTITIONER &&
        appointment.date.slice(0, 10) === today,
    ).length,
    openDrafts: coreData.visits.filter(
      (visit) => visit.practitioner === PRACTITIONER && visit.state === "draft",
    ).length,
    labsAwaiting: labRequests.filter(
      (row) => row.practitioner === PRACTITIONER && row.status === "received",
    ).length,
    followUps: followUpVisitsFor(PRACTITIONER).length,
  };
}

it("recomputes the four work counts from the sample rows", async () => {
  renderDoctorHome();
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  const counts = expectedCounts();
  for (const [key, value] of Object.entries(counts)) {
    const card = screen.getByTestId(`kpi-${key}`);
    expect(card.querySelector(".pc-kpi__value")).toHaveTextContent(
      String(value),
    );
  }
});

it("shows the next patient, availability and recent sample encounters", async () => {
  renderDoctorHome();
  const next = await screen.findByRole("heading", {
    name: patientLabel("P-10241", "th"),
  });
  expect(next).toBeVisible();
  expect(
    screen.getByRole("link", { name: /เปิดการตรวจ \/ ฉบับร่าง/ }),
  ).toHaveAttribute("href", ROUTES.visit("DC-VIS-2041"));

  // Availability is the authored sample schedule for the same clinician.
  const schedule = opsData.schedules.find(
    (row) => row.practitioner === PRACTITIONER,
  );
  expect(schedule).toBeDefined();
  expect(
    screen.getAllByText(new RegExp(`${schedule?.start}–${schedule?.end}`))
      .length,
  ).toBeGreaterThan(0);

  expect(screen.getByRole("heading", { name: "การตรวจล่าสุด" })).toBeVisible();
  expect(
    screen.getAllByRole("link", { name: "เปิดการตรวจ" }).length,
  ).toBeGreaterThan(0);
});

it("links the review count to the first sample result", async () => {
  renderDoctorHome();
  const firstLab = labRequests.find(
    (row) => row.practitioner === PRACTITIONER && row.status === "received",
  );
  expect(screen.getByTestId("kpi-labsAwaiting")).toHaveAttribute(
    "href",
    ROUTES.lab(firstLab?.id ?? ""),
  );
  expect(screen.getByTestId("kpi-openDrafts")).toHaveAttribute(
    "href",
    "#doctorDrafts",
  );
  expect(screen.getByTestId("kpi-followUps")).toHaveAttribute(
    "href",
    "#doctorFollowUps",
  );
});

it.each([390, 834, 1440] as const)(
  "keeps the dense day table inside the scroll wrapper at %i",
  async (width) => {
    setViewport(width);
    renderDoctorHome();
    const table = await screen.findByRole("table", { name: "ผู้ป่วยวันนี้" });
    expect(table.parentElement).toHaveClass("pc-tablewrap");
    expect(screen.getByTestId("sample-banner")).toBeVisible();
  },
);

it("renders the sample day network-free in native mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC")));
  renderWithFixtures(<DoctorHomePage />, {
    path: ROUTES.doctor,
    sessionMode: "live",
  });
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  // The surface reads authored rows directly; it never falls back to a proxy
  // read that a native build cannot serve.
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
  vi.unstubAllEnvs();
});

it("keeps the sample label above the day rows", async () => {
  renderDoctorHome();
  const banner = await screen.findByTestId("sample-banner");
  expect(within(banner).getByText("ข้อมูลตัวอย่าง")).toBeVisible();
  expect(
    screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
  ).toBeNull();
});

it("renders the designed English copy for the same sample day", async () => {
  renderWithFixtures(
    <LocaleProvider initial="en">
      <DoctorHomePage />
    </LocaleProvider>,
    { role: "DOC" },
  );
  expect(
    await screen.findByRole("heading", { name: "Doctor’s day" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "Next patient" })).toBeVisible();
  expect(screen.getAllByText(/Nattida Srisawat/).length).toBeGreaterThan(0);
});
