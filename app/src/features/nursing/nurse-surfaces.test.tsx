import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import {
  NATIVE_BOOTSTRAP,
  nativeSession,
  postSpy,
} from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { nurseCohort, patientLabel } from "../clinical/sample-scope";
import { NurseHomePage } from "./NurseHomePage";
import { NurseTasksPage } from "./NurseTasksPage";

function renderHome() {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.nurse} element={<NurseHomePage />} />
    </Routes>,
    { role: "NUR", path: ROUTES.nurse },
  );
}

function renderTasks(query: string) {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.nurseTasks} element={<NurseTasksPage />} />
    </Routes>,
    { role: "NUR", path: `${ROUTES.nurseTasks}${query}` },
  );
}

it("lists the assigned sample cohort with duties and upcoming procedures", async () => {
  const network = postSpy();
  renderHome();
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  const table = screen.getByRole("table", {
    name: "กลุ่มงานตัวอย่าง · ต้องยืนยันผู้รับผิดชอบ",
  });
  expect(within(table).getAllByRole("row")).toHaveLength(
    nurseCohort.length + 1,
  );
  expect(table).toHaveTextContent(patientLabel("P-10241", "th"));
  // Six designed duty tiles, and the photos tile points at the patient gallery.
  expect(document.querySelectorAll(".pc-tile")).toHaveLength(6);
  expect(
    screen.getByRole("link", { name: /ภาพถ่ายและความยินยอม/ }),
  ).toHaveAttribute("href", ROUTES.photos("P-10241"));
  expect(
    screen.getByRole("heading", { name: "หัตถการที่กำลังจะมาถึง" }),
  ).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("opens the first task with its patient and appointment context", async () => {
  renderHome();
  const links = await screen.findAllByRole("link", { name: "เปิดงาน" });
  expect(links[0]).toHaveAttribute(
    "href",
    `${ROUTES.nurseTasks}?patient=P-10241&appointment=DC-APT-3301&tab=vitals`,
  );
});

it("blocks a patient and appointment that do not belong together", async () => {
  renderTasks("?patient=P-10241&appointment=DC-APT-3303");
  expect(
    await screen.findByText(/บริบทผู้ป่วยและนัดหมายไม่ตรงกัน ไม่เปิดงาน/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "บันทึกสัญญาณชีพในเครื่อง" }),
  ).toBeNull();
});

it("validates and locally saves the vitals form", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=vitals");
  const temp = await screen.findByLabelText("อุณหภูมิ (°C)");
  expect(temp).toHaveValue(36.6);
  await user.clear(temp);
  await user.type(temp, "60");
  await user.click(screen.getByRole("button", { name: "บันทึกสัญญาณชีพในเครื่อง" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/ค่านอกช่วง/);

  await user.clear(temp);
  await user.type(temp, "36.8");
  await user.click(screen.getByRole("button", { name: "บันทึกสัญญาณชีพในเครื่อง" }));
  expect(await screen.findByText(/เก็บงานพยาบาลตัวอย่างในหน้านี้แล้ว/)).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("requires every preparation check before confirming readiness", async () => {
  const user = userEvent.setup();
  renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=prep");
  const consent = await screen.findByRole("checkbox", {
    name: "ตรวจหลักฐานความยินยอม",
  });
  expect(consent).toBeChecked();
  await user.click(consent);
  await user.click(screen.getByRole("button", { name: "ยืนยันเตรียมพร้อม" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ทำรายการตรวจสอบให้ครบ/,
  );
  await user.click(consent);
  await user.click(screen.getByRole("button", { name: "ยืนยันเตรียมพร้อม" }));
  expect(await screen.findByText(/เก็บงานพยาบาลตัวอย่างในหน้านี้แล้ว/)).toBeVisible();
});

it("requires acknowledgement before saving aftercare", async () => {
  const user = userEvent.setup();
  renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=aftercare");
  await user.type(
    await screen.findByLabelText("คำแนะนำที่ให้และอาการเฝ้าระวัง"),
    "หลีกเลี่ยงแสงแดดและติดตามอาการผื่นแดง",
  );
  fireEvent.change(screen.getByLabelText("วันติดตาม"), {
    target: { value: "2026-12-01" },
  });
  await user.click(screen.getByRole("button", { name: "บันทึกคำแนะนำ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ยืนยันผู้ป่วยรับทราบคำแนะนำ/,
  );
  await user.click(screen.getByRole("checkbox", { name: "ผู้ป่วยรับทราบคำแนะนำ" }));
  await user.click(screen.getByRole("button", { name: "บันทึกคำแนะนำ" }));
  expect(await screen.findByText(/เก็บงานพยาบาลตัวอย่างในหน้านี้แล้ว/)).toBeVisible();
});

it("dispatches the patient's pending lab requests to the lab workspace", async () => {
  renderTasks("?patient=P-10246&appointment=DC-APT-3306&tab=lab");
  const handoff = await screen.findByRole("link", { name: "เปิดส่งต่อ" });
  expect(handoff).toHaveAttribute("href", ROUTES.lab("DC-LAB-75"));
  expect(screen.getByText(/การส่งต่อแล็บแสดงผลเท่านั้น/)).toBeVisible();
});

it("hides requests that already left the dispatch queue", async () => {
  // DC-LAB-77 is received, so it is not dispatch work even though it belongs
  // to this patient's encounter.
  renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=lab");
  expect(
    await screen.findByText(/ไม่มีคำขอแล็บที่รอส่งต่อสำหรับผู้ป่วยรายนี้/),
  ).toBeVisible();
});

it("shows the designed empty state when the patient has no lab request", async () => {
  renderTasks("?patient=P-10243&appointment=DC-APT-3303&tab=lab");
  expect(
    await screen.findByText(/ไม่มีคำขอแล็บที่รอส่งต่อสำหรับผู้ป่วยรายนี้/),
  ).toBeVisible();
});

it("opens the first assigned task when no patient is addressed", async () => {
  renderTasks("");
  expect(await screen.findByTestId("nurse-task-context")).toBeVisible();
  expect(
    screen.getAllByText(new RegExp(patientLabel("P-10241", "th"))).length,
  ).toBeGreaterThan(0);
});

it.each([390, 834, 1440] as const)(
  "keeps the nursing tasks usable at %i",
  async (width) => {
    setViewport(width);
    renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=vitals");
    expect(await screen.findByTestId("sample-banner")).toBeVisible();
    expect(
      screen.getAllByRole("button", { name: /สัญญาณชีพ/ }).length,
    ).toBeGreaterThan(0);
    expect(screen.getByTestId("nurse-task-context")).toBeVisible();
  },
);

it("renders the nursing cohort from sample rows without network in native mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("NUR")));
  renderWithFixtures(<NurseHomePage />, {
    path: ROUTES.nurse,
    sessionMode: "live",
  });
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
  vi.unstubAllEnvs();
});

it.each([390, 834, 1440] as const)(
  "keeps the cohort page usable at %i",
  async (width) => {
    setViewport(width);
    renderHome();
    expect(await screen.findByTestId("sample-banner")).toBeVisible();
    const table = screen.getByRole("table", {
      name: "กลุ่มงานตัวอย่าง · ต้องยืนยันผู้รับผิดชอบ",
    });
    expect(table.parentElement).toHaveClass("pc-tablewrap");
  },
);

it("saves nursing work locally without a transport", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderTasks("?patient=P-10241&appointment=DC-APT-3301&tab=vitals");
  await user.click(
    await screen.findByRole("button", { name: "บันทึกสัญญาณชีพในเครื่อง" }),
  );
  expect(await screen.findByText(/เก็บงานพยาบาลตัวอย่างในหน้านี้แล้ว/)).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});
