import clinical from "@clinic/contracts/fixtures/clinical.json";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link, Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { App } from "../../app/App";
import { createHandlers } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../test/render";
import { VisitHeader } from "./VisitHeader";
import { VisitWorkspacePage } from "./VisitWorkspacePage";

function renderVisit(path = "/clinic/visits/ENC-1") {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
    </Routes>,
    { role: "DOC", path },
  );
}

it("keeps the encounter id across section changes and supports keyboard tabs", async () => {
  setViewport(1440);
  renderVisit();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-1");
  const tab = screen.getByRole("tab", { name: /ประวัติ|history/i });
  await userEvent.click(tab);
  expect(tab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("encounter-id")).toHaveTextContent("ENC-1");
  await userEvent.keyboard("{ArrowRight}");
  expect(
    screen.getByRole("tab", { name: /ตรวจร่างกาย|examination/i }),
  ).toHaveAttribute("aria-selected", "true");
});

it("shows not found for an unknown visit", async () => {
  renderVisit("/clinic/visits/UNKNOWN");
  expect(await screen.findByText(/ไม่พบ|not found/i)).toBeVisible();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByTestId("encounter-id")).toBeNull();
});

it("stacks sections as accordions at 390 with only one open", async () => {
  setViewport(390);
  renderVisit();
  expect(await screen.findByTestId("section-accordion")).toBeVisible();
  const history = screen.getByRole("button", { name: /ประวัติ|history/i });
  await userEvent.click(history);
  expect(history).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(1);
  expect(screen.getByTestId("encounter-id")).toHaveTextContent("ENC-1");
});

it("opens the addressed encounter and section from a deep link", async () => {
  setViewport(834);
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
    </Routes>,
    {
      role: "DOC",
      encounterId: "ENC-2",
      path: "/clinic/visits/ENC-2?section=note",
    },
  );
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-2");
  expect(screen.getByRole("tab", { name: /บันทึก|note/i })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

it("shows the privacy notice when shared-device flag is set", async () => {
  renderWithFixtures(
    <VisitHeader
      sharedDevice
      context={{
        encounterId: "ENC-1",
        patientId: "PAT-1",
        patientName: "Patient",
        practitionerName: "Doctor",
        startedAt: "2026-11-05T02:00:00Z",
        state: "open",
      }}
    />,
    { role: "DOC" },
  );
  expect(await screen.findByText(/อุปกรณ์ใช้ร่วมกัน|shared device/i)).toBeVisible();
});

function expectNoWorkspace() {
  expect(screen.queryByTestId("encounter-id")).toBeNull();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByTestId("section-accordion")).toBeNull();
  expect(
    screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
  ).toBeNull();
}

it("removes old identity while a mounted route changes encounter", async () => {
  setViewport(1440);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  renderWithFixtures(
    <>
      <Link to="/clinic/visits/ENC-2">Next encounter</Link>
      <Routes>
        <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
      </Routes>
    </>,
    { role: "DOC", path: "/clinic/visits/ENC-1" },
  );
  server.use(
    http.get("/api/read/clinical-visits", async ({ request }) => {
      const filters = JSON.parse(
        new URL(request.url).searchParams.get("filters") ?? "{}",
      );
      if (filters.encounterId === "ENC-2") await pending;
      const items = clinical.visits.filter(
        (visit) => visit.encounterId === filters.encounterId,
      );
      return HttpResponse.json({ items, total: items.length });
    }),
  );
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-1");
  await userEvent.click(screen.getByRole("link", { name: "Next encounter" }));
  expectNoWorkspace();
  release();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-2");
});

it("fails closed for a mismatched encounter response", async () => {
  renderVisit();
  server.use(
    http.get("/api/read/clinical-visits", () =>
      HttpResponse.json({ items: [clinical.visits[1]], total: 1 }),
    ),
  );
  expect(await screen.findByText(/ไม่พบ|not found/i)).toBeVisible();
  expectNoWorkspace();
});

it("fails closed for a forbidden response", async () => {
  renderVisit();
  server.use(
    http.get("/api/read/clinical-visits", () =>
      HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
    ),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ไม่มีสิทธิ์|does not have permission/i,
  );
  expectNoWorkspace();
});

it.each(["REC", "PHA", "CAS", "MGR", "ADM"] as const)(
  "denies %s via real router, session and fixture read policy",
  async (role) => {
    setViewport(1440);
    window.history.pushState({}, "", "/clinic/visits/ENC-1");
    server.use(...createHandlers({ role }));
    render(<App />);
    // The designed role matrix keeps these roles out of the clinical workspace,
    // so the route guard states the denial before any clinical read starts.
    expect(await screen.findByTestId("role-guard-denied")).toHaveAttribute(
      "data-surface",
      "DOC-02",
    );
    expect(screen.getByText(/ไม่มีสิทธิ์เข้าถึงหน้านี้/)).toBeVisible();
    expectNoWorkspace();
  },
);
