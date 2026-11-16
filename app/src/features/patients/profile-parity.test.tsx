import frontdesk from "@clinic/contracts/fixtures/frontdesk.json";
import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import type { RoleCode } from "../../domain/capability";
import { server } from "../../test/msw/server";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { renderWithFixtures, setViewport } from "../../test/render";
import { PatientProfilePage } from "./PatientProfilePage";

const PROFILE_ID = frontdesk.patients[0]?.id ?? "fixture-dir-01";

function renderProfile(role: RoleCode = "REC") {
  return renderWithFixtures(
    <Routes>
      <Route path={`${ROUTES.patients}/:id`} element={<PatientProfilePage />} />
    </Routes>,
    { role, path: ROUTES.patient(PROFILE_ID) },
  );
}

it("shows the last visit and latest practitioner in the summary card", async () => {
  renderProfile();
  const summary = await screen.findByRole("region", { name: "สรุปผู้ป่วย" });
  expect(within(summary).getByText("28/10/2026")).toBeVisible();
  expect(within(summary).getByText("Fixture Doctor")).toBeVisible();
  expect(
    within(summary).getByRole("link", { name: "เปิดภาพและหลักฐาน" }),
  ).toHaveAttribute("href", ROUTES.photos(PROFILE_ID));
});

it("reads an empty visit history as no previous visit, not a blank", async () => {
  renderProfile();
  server.use(
    http.get("*/api/read/frontdesk-patients", () =>
      HttpResponse.json({
        items: [
          {
            id: "fixture-dir-07",
            fullName: "Pimchanok Srisuk",
            mobileMasked: "087-XXX-2201",
            fictional: true,
          },
        ],
        total: 1,
      }),
    ),
  );
  const summary = await screen.findByRole("region", { name: "สรุปผู้ป่วย" });
  expect(within(summary).getByText("ยังไม่มีประวัติการมาเยือน")).toBeVisible();
  expect(within(summary).getByText("ไม่พร้อมใช้งาน")).toBeVisible();
});

it("labels authored consent evidence as Demo/Sample and shows signer and scope", async () => {
  renderProfile();
  server.use(
    http.get("*/api/read/frontdesk-patients", () =>
      HttpResponse.json({
        items: [
          {
            id: "P-10241",
            fullName: "ณัฐธิดา ศรีสวัสดิ์",
            mobileMasked: "081-XXX-5678",
            fictional: true,
            lastVisitAt: "2026-10-28",
            latestPractitioner: "Dr. Kitti Wannachai",
          },
        ],
        total: 1,
      }),
    ),
  );
  const consent = await screen.findByRole("region", {
    name: "หลักฐานความยินยอมภาพถ่าย",
  });
  expect(within(consent).getByTestId("sample-banner")).toBeVisible();
  expect(within(consent).getByText("ให้ความยินยอม")).toBeVisible();
  expect(within(consent).getByText(/ณัฐธิดา ศรีสวัสดิ์/)).toBeVisible();
  expect(within(consent).getAllByText(/PC-2\.1/).length).toBeGreaterThan(0);
  expect(
    within(consent).getByText("ภาพเพื่อการรักษาเท่านั้น"),
  ).toBeVisible();
});

it("shows the designed empty consent state for a patient without evidence", async () => {
  renderProfile();
  const consent = await screen.findByRole("region", {
    name: "หลักฐานความยินยอมภาพถ่าย",
  });
  expect(
    within(consent).getByText("ยังไม่มีหลักฐานความยินยอมสำหรับผู้ป่วยรายนี้"),
  ).toBeVisible();
  expect(within(consent).queryByText("ให้ความยินยอม")).toBeNull();
});

it.each([
  ["REC", "รายละเอียดทางคลินิกจำกัดเฉพาะทีมรักษา"],
  ["NUR", "รายละเอียดทางคลินิกจำกัดเฉพาะทีมรักษา"],
  ["DOC", "บทบาทนี้เห็นข้อมูลทางคลินิกทั้งหมดของเจ้าของการตรวจ"],
  ["ADM", "รายละเอียดทางคลินิกจำกัดเฉพาะทีมรักษา"],
] as const)("explains the %s clinical redaction scope", async (role, text) => {
  renderProfile(role);
  const notice = await screen.findByTestId("redaction-notice");
  expect(within(notice).getByText(text)).toBeVisible();
});

it("keeps the role's history tabs beside the new summary blocks", async () => {
  renderProfile("DOC");
  expect(await screen.findByRole("tab", { name: "นัดหมาย" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "การตรวจ" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "แล็บ" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "การรักษา" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "รูปภาพ" })).toBeVisible();
  // Payment history stays a cashier/management tab.
  expect(screen.queryByRole("tab", { name: "การชำระเงิน" })).toBeNull();
});

it.each([390, 834, 1440] as const)(
  "keeps the summary additions in normal flow at %spx",
  async (width) => {
    setViewport(width);
    renderProfile();
    const grid = await screen.findByRole("region", { name: "สรุปผู้ป่วย" });
    expect(grid.closest(".patients-summary-grid")).not.toBeNull();
    expect(
      screen.getByRole("region", { name: "หลักฐานความยินยอมภาพถ่าย" }),
    ).toBeVisible();
    expect(screen.getByTestId("redaction-notice")).toBeVisible();
    // The three summary cards are siblings of one shrinking grid, so a narrow
    // viewport stacks them instead of clipping one.
    expect(
      document.querySelectorAll(".patients-summary-grid > *"),
    ).toHaveLength(3);
  },
);

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

it("keeps the native profile fixture-free and write-free", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const writes: string[] = [];
  const listener = (event: { request: Request }) => {
    if (event.request.method !== "GET") writes.push(event.request.url);
  };
  server.events.on("request:start", listener);
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
      HttpResponse.json({
        message: {
          mode: "live",
          sessionEpoch: "native-epoch",
          user: {
            id: "native|doc",
            displayName: "Native Doctor",
            roles: ["DOC"],
          },
          capabilities: { "read.patient": "enabled" },
          csrfToken: "native-csrf",
        },
      }),
    ),
    http.get("/api/method/clinic_ui.api.patients.view", () =>
      HttpResponse.json({
        message: {
          workspace_patient_id: "WS-1",
          display_name: "Native Patient",
          first_name: "Native",
          last_name: "Patient",
          sex: "Female",
          dob: "1990-01-02",
          status: "Active",
          modified: "v1",
          editable: true,
        },
      }),
    ),
  );
  renderWithFixtures(
    <Routes>
      <Route path={`${ROUTES.patients}/:id`} element={<PatientProfilePage />} />
    </Routes>,
    { role: "DOC", path: ROUTES.patient("WS-1"), sessionMode: "live" },
  );
  expect(
    await screen.findByRole("heading", { name: "Native Patient" }),
  ).toBeVisible();
  // The role notice is design copy, not a fixture record, and the authored
  // consent block is never borrowed into a native read.
  expect(screen.getByTestId("redaction-notice")).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "หลักฐานความยินยอมภาพถ่าย" }),
  ).toBeNull();
  expect(writes).toEqual([]);
  server.events.removeListener("request:start", listener);
});
