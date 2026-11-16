import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { coreData } from "../../fixtures/data";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { postSpy } from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { AuthoredVisitWorkspace } from "./AuthoredVisitWorkspace";
import { VisitWorkspacePage } from "./VisitWorkspacePage";

let restoreDialog: () => void;
beforeAll(() => {
  restoreDialog = installDialogMethods();
});
afterAll(() => {
  restoreDialog();
});

function renderAuthored() {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.visitPattern} element={<VisitWorkspacePage />} />
    </Routes>,
    { role: "DOC", path: ROUTES.visit("DC-VIS-2041") },
  );
}

it("opens the authored sample encounter with every designed section", async () => {
  renderAuthored();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent(
    "DC-VIS-2041",
  );
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(10);
  for (const label of [
    "ภาพรวม",
    "ประวัติ",
    "การตรวจร่างกาย",
    "บันทึก",
    "การวินิจฉัย",
    "ยา",
    "หัตถการ",
    "ผลแล็บ",
    "ภาพถ่าย",
    "สรุป",
  ])
    expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeVisible();
  // The three former placeholders are real designed sections now.
  expect(
    screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
  ).toBeNull();
});

it("guides readiness from the canonical fields", async () => {
  const user = userEvent.setup();
  renderAuthored();
  const readiness = await screen.findByRole("status");
  expect(readiness).toHaveTextContent("ครบแล้ว ตรวจทานก่อนยืนยัน");

  await user.click(screen.getByRole("tab", { name: /ประวัติ|history/i }));
  const complaint = await screen.findByLabelText("อาการสำคัญ");
  await user.clear(complaint);
  expect(screen.getByRole("status")).toHaveTextContent("ยังต้องกรอก");
  expect(screen.getByRole("status")).toHaveTextContent("อาการสำคัญ");
});

it("shows the patient's external lab request and the procedure link", async () => {
  const user = userEvent.setup();
  renderAuthored();
  await user.click(await screen.findByRole("tab", { name: /ผลแล็บ|lab/i }));
  expect(await screen.findByRole("link", { name: "เปิดคำขอ" })).toHaveAttribute(
    "href",
    ROUTES.lab("DC-LAB-77"),
  );
  expect(screen.getByRole("heading", { name: "ผลตรวจในคลินิก" })).toBeVisible();

  await user.click(screen.getByRole("tab", { name: /หัตถการ|procedures/i }));
  expect(
    await screen.findByRole("link", { name: "เตรียมและยืนยันใช้จริง" }),
  ).toHaveAttribute("href", ROUTES.procedure("DC-PROC-118"));
});

it("keeps the new lab request control explicitly unavailable", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(await screen.findByRole("tab", { name: /ผลแล็บ|lab/i }));
  const control = await screen.findByRole("button", { name: "สั่งตรวจใหม่" });
  expect(control).toBeDisabled();
  expect(control).toHaveAccessibleDescription(
    "การสั่งตรวจใหม่ไม่มีในสัญญานี้ ระบบจะไม่ส่งคำขอ",
  );
  await user.click(control);
  expect(network.posts).toEqual([]);
  network.stop();
});

it("keeps a local draft inside the screen and sends nothing", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(await screen.findByRole("button", { name: "บันทึกฉบับร่าง" }));
  const status = screen.getByTestId("visit-save-status");
  expect(status).toHaveTextContent("ไม่ได้ส่งไปเซิร์ฟเวอร์");
  expect(network.posts).toEqual([]);
  network.stop();
});

it("locks the sample encounter once completed and keeps an amendment apart", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบการตรวจตัวอย่าง" }),
  );
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("ไม่ใช่การลงนามแพทย์จริง");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));

  await waitFor(() =>
    expect(screen.getByTestId("visit-save-status")).toHaveTextContent(
      "เสร็จสิ้นตัวอย่าง",
    ),
  );
  expect(screen.getByRole("button", { name: "บันทึกฉบับร่าง" })).toBeDisabled();

  // Navigate to the review section: the amendment control appears only here.
  await user.click(screen.getByRole("tab", { name: /สรุป|review/i }));
  await user.click(
    await screen.findByRole("button", { name: "แก้ไขหลังลงนาม (คำขอใหม่)" }),
  );
  const amend = await screen.findByRole("dialog");
  const confirm = within(amend).getByRole("button", { name: "ยืนยัน" });
  expect(confirm).toBeDisabled();
  await user.type(
    within(amend).getByLabelText("เหตุผลและข้อความเพิ่มเติม"),
    "เพิ่มความรุนแรงของสิวตามภาพถ่าย",
  );
  await user.click(confirm);
  expect(await screen.findByTestId("visit-addendum")).toHaveTextContent(
    "ภาคผนวกแยก",
  );
  expect(screen.getByText("เก็บคำขอแก้ไขแยก ไม่ทับต้นฉบับ")).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("keeps the desktop rail at the tablet breakpoint", async () => {
  setViewport(834);
  renderAuthored();
  expect(await screen.findByRole("tablist")).toBeVisible();
  expect(screen.queryByTestId("section-accordion")).toBeNull();
});

it("stacks the authored editor as one-open accordions at 390", async () => {
  setViewport(390);
  renderAuthored();
  expect(await screen.findByTestId("section-accordion")).toBeVisible();
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(1);
});

it("renders the authored editor without a proxy read", async () => {
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  const visit = coreData.visitById("DC-VIS-2041");
  if (!visit) throw new Error("fixture encounter missing");
  renderWithFixtures(<AuthoredVisitWorkspace visit={visit} />, {
    role: "DOC",
  });
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
});
