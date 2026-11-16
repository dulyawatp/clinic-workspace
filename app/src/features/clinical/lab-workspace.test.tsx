import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import {
  NATIVE_BOOTSTRAP,
  nativeSession,
  postSpy,
} from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { LabWorkspacePage } from "./LabWorkspacePage";
import { labRequests } from "./sample-scope";

let restoreDialog: () => void;
beforeAll(() => {
  restoreDialog = installDialogMethods();
});
afterAll(() => {
  restoreDialog();
});

function renderLab(id: string, role: "DOC" | "NUR" = "DOC") {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.labPattern} element={<LabWorkspacePage />} />
    </Routes>,
    { role, path: ROUTES.lab(id) },
  );
}

it("offers all six designed states as a worklist filter", async () => {
  renderLab("DC-LAB-77");
  const select = await screen.findByLabelText("สถานะ");
  const options = within(select).getAllByRole("option");
  expect(options).toHaveLength(7);
  for (const value of [
    "all",
    "ordered",
    "collected",
    "sent",
    "received",
    "reviewed",
    "cancelled",
  ])
    expect(options.map((option) => option.getAttribute("value"))).toContain(
      value,
    );
});

it("renders the 18-field request and result panel with authored values", async () => {
  renderLab("SAMPLE-LAB-78");
  const workspace = await screen.findByTestId("lab-workspace");
  const fields = workspace.querySelectorAll('[data-lab-fields="18"] dt');
  expect(fields).toHaveLength(18);
  const table = screen.getByRole("table", { name: "รายการแล็บภายนอก" });
  expect(table.parentElement).toHaveClass("pc-tablewrap");
  expect(workspace).toHaveTextContent("Benign inflammatory dermatosis");
  expect(workspace).toHaveTextContent("พยาธิวิทยา");
  // The private sample file is displayed without pretending it is reachable.
  expect(workspace).toHaveTextContent("pathology-78.pdf");
  expect(within(workspace).getAllByText("ไฟล์ส่วนตัว").length).toBeGreaterThan(0);
  expect(workspace).toHaveTextContent("เปิดไฟล์ยังไม่พร้อมใช้งาน");
});

it("presents the review state but keeps the write unavailable", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderLab("DC-LAB-77");
  expect(await screen.findByText("BCL-88412")).toBeVisible();
  expect(screen.queryByText(/ยังไม่เชื่อมการตรวจ/)).toBeNull();
  await user.click(screen.getByRole("button", { name: "ตรวจทานผล" }));
  const drawer = await screen.findByTestId("lab-drawer-review");
  expect(drawer).toHaveTextContent("ลายมือชื่อแพทย์ที่เซิร์ฟเวอร์รับรอง");
  expect(
    within(drawer.closest("dialog") as HTMLElement).getByRole("button", {
      name: "ไม่พร้อมใช้งานในระบบนี้",
    }),
  ).toBeDisabled();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("shows a collected sample with its own evidence", async () => {
  renderLab("SAMPLE-LAB-79");
  const collected = await screen.findByTestId("lab-collected");
  expect(collected).toHaveTextContent("เก็บตัวอย่างแล้ว");
  expect(screen.getByTestId("lab-workspace")).toHaveTextContent("เร่งด่วน");
});

it("shows a cancelled sample with its cancellation reason", async () => {
  renderLab("SAMPLE-LAB-80");
  const cancelled = await screen.findByTestId("lab-cancelled");
  expect(cancelled).toHaveTextContent("ผู้ป่วยขอเลื่อนการตรวจ");
});

it("does not present the receiver as the uploader", async () => {
  renderLab("DC-LAB-77");
  expect(
    await screen.findByText(/รับผลโดย maneerat · ไม่ได้บันทึกผู้อัปโหลด/),
  ).toBeVisible();
});

it("filters the worklist to cancelled requests", async () => {
  const user = userEvent.setup();
  renderLab("DC-LAB-77");
  const select = await screen.findByLabelText("สถานะ");
  await user.selectOptions(select, "cancelled");
  const table = screen.getByRole("table", { name: "รายการแล็บภายนอก" });
  expect(table).toHaveTextContent("SAMPLE-LAB-80");
  expect(table).not.toHaveTextContent("DC-LAB-77");
});

it("limits receipt to nurses and keeps send/receive unavailable", async () => {
  renderLab("DC-LAB-77", "NUR");
  const receive = await screen.findByRole("button", {
    name: "แนบผล / รับผล",
  });
  expect(receive).toBeEnabled();
  expect(screen.queryByRole("button", { name: "ตรวจทานผล" })).toBeNull();
  expect(screen.queryByRole("button", { name: "บันทึกส่งตัวอย่าง" })).toBeNull();
});

it("flags requests without a verified visit link", async () => {
  renderLab("SAMPLE-LAB-79", "NUR");
  expect(
    await screen.findByText(/ยังไม่เชื่อมการตรวจหรือผู้ให้บริการที่ยืนยัน/),
  ).toBeVisible();
});

it("shows the designed not-found state for an unknown request", async () => {
  renderLab("unknown-1");
  expect(await screen.findByText(/ไม่พบคำขอแล็บ/i)).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
});

it("reads the sample lab rows without a proxy read in native mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC")));
  renderWithFixtures(
    <Routes>
      <Route path={ROUTES.labPattern} element={<LabWorkspacePage />} />
    </Routes>,
    {
      path: ROUTES.lab(labRequests[0]?.id ?? "DC-LAB-77"),
      sessionMode: "live",
    },
  );
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
  vi.unstubAllEnvs();
});

it.each([390, 834, 1440] as const)(
  "keeps the lab workspace usable at %i",
  async (width) => {
    setViewport(width);
    renderLab("DC-LAB-77");
    expect(await screen.findByTestId("sample-banner")).toBeVisible();
    const table = screen.getByRole("table", { name: "รายการแล็บภายนอก" });
    expect(table.parentElement).toHaveClass("pc-tablewrap");
  },
);
