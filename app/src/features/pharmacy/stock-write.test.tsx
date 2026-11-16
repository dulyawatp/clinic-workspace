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
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, caps)));
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

it("transfers between two warehouses with one acknowledgement", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_STOCK);
  server.use(
    http.post(TRANSFER, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "transferred",
          movement: {
            kind: "stock_transfer",
            item_code: "ITEM-1",
            warehouse: "WH-1",
            target_warehouse: "WH-2",
            qty: 2,
            batch: "BATCH-1",
          },
          ackToken: TOKEN,
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
    item_code: "ITEM-1",
    qty: 2,
    source_warehouse: "WH-1",
    target_warehouse: "WH-2",
    batch_id: "BATCH-1",
  });
  expect(await screen.findByText("โอนย้ายแล้ว")).toBeTruthy();
});

it("records the reconciliation reason and refuses a server validation", async () => {
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
    name: "กระทบยอดสินค้า (จำกัดสิทธิ์)",
  });
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนที่นับได้"), "3");
  await user.type(within(form).getByLabelText("คลัง"), "WH-1");
  await user.type(within(form).getByLabelText("เหตุผล"), "Count discrepancy");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันกระทบยอด" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    item_code: "ITEM-1",
    qty: 3,
    warehouse: "WH-1",
    reason: "Count discrepancy",
  });
  expect(
    await screen.findByText(
      /เซิร์ฟเวอร์ปฏิเสธรายการนี้ ตรวจสินค้า ล็อต คลัง จำนวน หรือเอกสารอ้างอิง/,
    ),
  ).toBeTruthy();
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
