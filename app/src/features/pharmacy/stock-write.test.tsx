import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { PharmacyMovementsPage } from "./PharmacyMovementsPage";
import { PharmacyReceivePage } from "./PharmacyReceivePage";

const RECEIVE = "/api/method/clinic_ui.api.stock_write.receive";
const TRANSFER = "/api/method/clinic_ui.api.stock_write.transfer";
const ADJUST = "/api/method/clinic_ui.api.stock_write.adjust";
const ACK = "/api/method/clinic_ui.api.stock_write.ack";
const TOKEN = "a".repeat(64);

const ENABLED_STOCK = {
  "write.stock.receive": "enabled",
  "write.stock.transfer": "enabled",
  "write.stock.adjust": "enabled",
} as const;

function serve(caps: Record<string, string>, role: "PHA" | "ADM" = "PHA") {
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, caps)),
    http.get("/api/resource/Stock%20Ledger%20Entry", () => HttpResponse.json({ data: [] })),
  );
}

function ack() {
  return HttpResponse.json({ message: { acknowledged: true } });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("hides every stock form while the capabilities are read_only", async () => {
  serve({ "read.patient": "enabled" });
  renderWithFixtures(<PharmacyReceivePage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyReceive,
  });
  await screen.findByTestId("pharmacy-receive");
  expect(screen.queryByTestId("stock-write-form")).toBeNull();
});

it("receives one line, acks once, and hides the native reference", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_STOCK);
  server.use(
    http.post(RECEIVE, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "received",
          movement: {
            kind: "stock_receive",
            item_code: "ITEM-1",
            warehouse: "WH-1",
            qty: 4,
            batch: "BATCH-1",
          },
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PharmacyReceivePage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyReceive,
  });
  const form = await screen.findByTestId("stock-write-form");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวน"), "4");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.type(within(form).getByLabelText("ผู้จำหน่าย"), "SUP-1");
  await user.type(within(form).getByLabelText("เลขล็อต"), "BATCH-1");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันรับสินค้า" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    item_code: "ITEM-1",
    qty: 4,
    warehouse: "WH-1",
    supplier: "SUP-1",
    batch_id: "BATCH-1",
  });
  expect(typeof bodies[0]?.operation_key).toBe("string");
  expect(bodies[0]).not.toHaveProperty("docstatus");
  expect(await screen.findByText("รับสินค้าแล้ว")).toBeTruthy();
  expect(within(form).queryByText(/PR-/)).toBeNull();
});

it("transfers between two warehouses and shows the native reference", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_STOCK);
  server.use(
    http.post(TRANSFER, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "transferred",
          stock_entry: { stock_entry_id: "STE-1", docstatus: 1 },
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PharmacyMovementsPage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyMovements,
  });
  const form = await screen.findByRole("region", {
    name: "โอนย้ายสินค้าระหว่างคลัง",
  });
  await user.type(within(form).getByLabelText("บริษัท"), "Clinic");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวน"), "2");
  await user.type(within(form).getByLabelText("คลังต้นทาง"), "WH-1");
  await user.type(within(form).getByLabelText("คลังปลายทาง"), "WH-2");
  await user.type(within(form).getByLabelText("เลขล็อต"), "BATCH-1");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันโอนย้าย" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    company: "Clinic",
    item_code: "ITEM-1",
    qty: 2,
    source_warehouse: "WH-1",
    target_warehouse: "WH-2",
    batch_id: "BATCH-1",
  });
  expect(await screen.findByText("โอนย้ายแล้ว: STE-1")).toBeTruthy();
});

it("sends native reconciliation fields and shows server validation", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_STOCK, "ADM");
  server.use(
    http.post(ADJUST, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(
        { message: { code: "VALIDATION" } },
        { status: 417 },
      );
    }),
  );
  renderWithFixtures(<PharmacyMovementsPage />, {
    role: "ADM",
    sessionMode: "live",
    path: ROUTES.pharmacyMovements,
  });
  const form = await screen.findByRole("region", {
    name: "กระทบยอดสินค้าบนเซิร์ฟเวอร์",
  });
  expect(screen.queryByText(/แบบฟอร์มนี้เก็บข้อมูลตัวอย่างในเบราว์เซอร์เท่านั้น/)).not.toBeInTheDocument();
  expect(within(form).getByText(/ส่งรายการกระทบยอดไปยังเซิร์ฟเวอร์และเปลี่ยนสต็อก/)).toBeTruthy();
  expect(within(form).getByRole("option", { name: "รายการปรับปรุง" })).toBeTruthy();
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนที่นับได้"), "3");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.selectOptions(within(form).getByLabelText("วัตถุประสงค์"), "Opening Stock");
  await user.type(within(form).getByLabelText("บริษัท"), "Clinic");
  await user.type(within(form).getByLabelText("ราคาประเมิน (ไม่บังคับ)"), "5");
  await user.type(within(form).getByLabelText("บัญชีค่าใช้จ่าย / ส่วนต่าง (ไม่บังคับ)"), "Opening - C");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันกระทบยอด" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    item_code: "ITEM-1",
    qty: 3,
    warehouse: "WH-1",
    purpose: "Opening Stock",
    company: "Clinic",
    valuation_rate: 5,
    expense_account: "Opening - C",
  });
  expect(bodies[0]).not.toHaveProperty("reason");
  expect(
    await screen.findByText(
      /เซิร์ฟเวอร์ปฏิเสธรายการนี้ ตรวจสินค้า ล็อต คลัง จำนวน หรือเอกสารอ้างอิง/,
    ),
  ).toBeTruthy();
});

it("reconciles with one POST and no acknowledgement or persisted lock", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  let acks = 0;
  serve(ENABLED_STOCK, "ADM");
  server.use(
    http.post(ADJUST, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "adjusted",
          reconciliation: { reconciliation_id: "SR-1", docstatus: 1 },
        },
      });
    }),
    http.post(ACK, () => {
      acks += 1;
      return ack();
    }),
  );
  renderWithFixtures(<PharmacyMovementsPage />, {
    role: "ADM",
    sessionMode: "live",
    path: ROUTES.pharmacyMovements,
  });
  const form = await screen.findByRole("region", {
    name: "กระทบยอดสินค้าบนเซิร์ฟเวอร์",
  });
  await user.selectOptions(within(form).getByLabelText("วัตถุประสงค์"), "Adjustment Entry");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนที่นับได้"), "3");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.type(within(form).getByLabelText("บริษัท"), "Clinic");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันกระทบยอด" }),
  );

  expect(await screen.findByText("กระทบยอดแล้ว · SR-1")).toBeTruthy();
  expect(bodies).toHaveLength(1);
  expect(bodies[0]).toMatchObject({ purpose: "Adjustment Entry" });
  expect(bodies[0]).not.toHaveProperty("valuation_rate");
  expect(bodies[0]).not.toHaveProperty("expense_account");
  expect(acks).toBe(0);
  expect(
    Object.keys(localStorage).filter((key) =>
      key.includes("stock-write.pending"),
    ),
  ).toEqual([]);
});

it("shows an unknown outcome for a malformed success without retry or lock", async () => {
  const user = userEvent.setup();
  let calls = 0;
  serve(ENABLED_STOCK, "ADM");
  server.use(
    http.post(ADJUST, () => {
      calls += 1;
      return HttpResponse.json({ message: { status: "adjusted" } });
    }),
  );
  renderWithFixtures(<PharmacyMovementsPage />, {
    role: "ADM",
    sessionMode: "live",
    path: ROUTES.pharmacyMovements,
  });
  const form = await screen.findByRole("region", {
    name: "กระทบยอดสินค้าบนเซิร์ฟเวอร์",
  });
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนที่นับได้"), "3");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.type(within(form).getByLabelText("บริษัท"), "Clinic");
  await user.type(within(form).getByLabelText("ราคาประเมิน (ไม่บังคับ)"), "5");
  const submit = within(form).getByRole("button", { name: "ยืนยันกระทบยอด" });
  await user.click(submit);

  expect(
    await screen.findByText(
      /ผลลัพธ์ไม่แน่นอน ตรวจสอบรายการกระทบยอดสินค้าในระบบ Frappe ก่อนลองอีกครั้ง/,
    ),
  ).toBeTruthy();
  expect((submit as HTMLButtonElement).disabled).toBe(false);
  expect(within(form).queryByText(/กระทบยอดแล้ว/)).toBeNull();
  expect(calls).toBe(1);
});

it("keeps the lock on an uncertain outcome and never retries", async () => {
  const user = userEvent.setup();
  let calls = 0;
  serve(ENABLED_STOCK);
  server.use(
    http.post(RECEIVE, () => {
      calls += 1;
      return HttpResponse.json(
        { message: { code: "WRITE_LOCKED" } },
        { status: 409 },
      );
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PharmacyReceivePage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyReceive,
  });
  const form = await screen.findByTestId("stock-write-form");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวน"), "4");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.type(within(form).getByLabelText("ผู้จำหน่าย"), "SUP-1");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันรับสินค้า" }),
  );

  expect(
    await screen.findByText(
      /ผลลัพธ์ไม่แน่นอน ห้ามลองซ้ำ ให้ผู้ปฏิบัติงานตรวจสอบก่อน/,
    ),
  ).toBeTruthy();
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันรับสินค้า" }),
  );
  expect(calls).toBe(1);
});
