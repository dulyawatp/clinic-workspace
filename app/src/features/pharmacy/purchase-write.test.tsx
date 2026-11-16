import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { PharmacyPurchasingPage } from "./PharmacyPurchasingPage";
import { PharmacyReceivePage } from "./PharmacyReceivePage";

const DRAFT = "/api/method/clinic_ui.api.purchase_write.save_po_draft";
const SUBMIT = "/api/method/clinic_ui.api.purchase_write.submit_po";
const RECEIVE = "/api/method/clinic_ui.api.purchase_write.receive";
const ACK = "/api/method/clinic_ui.api.purchase_write.ack";
const TOKEN = "a".repeat(64);

const ENABLED = {
  "write.purchase_order.draft": "enabled",
  "write.purchase_order.submit": "enabled",
  "write.stock.receive": "enabled",
} as const;

function serve(caps: Record<string, string>, role: "PHA" | "MGR" = "PHA") {
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

it("hides every purchasing form while the capabilities are read_only", async () => {
  serve({ "read.patient": "enabled" });
  renderWithFixtures(<PharmacyPurchasingPage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyPurchasing,
  });
  await screen.findByTestId("pharmacy-purchasing");
  expect(screen.queryByTestId("purchase-write-form")).toBeNull();
});

it("drafts one purchase order with itemized lines and no docstatus", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(DRAFT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "drafted",
          purchase_order: "PO-1",
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PharmacyPurchasingPage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyPurchasing,
  });
  const form = await screen.findByTestId("purchase-write-form");
  await user.type(within(form).getByLabelText("ผู้จำหน่าย"), "SUP-1");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวน"), "5");
  await user.type(within(form).getByLabelText("ราคาต่อหน่วย"), "2.5");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันสร้างใบสั่งซื้อ" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    supplier: "SUP-1",
    items: [{ item_code: "ITEM-1", qty: 5, rate: 2.5 }],
  });
  expect(typeof bodies[0]?.operation_key).toBe("string");
  expect(bodies[0]).not.toHaveProperty("docstatus");
  expect(await screen.findByText("สร้างใบสั่งซื้อแล้ว")).toBeTruthy();
});

it("submits an existing draft and shows the server status", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED, "MGR");
  server.use(
    http.post(SUBMIT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "submitted",
          purchase_order: "PO-1",
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PharmacyPurchasingPage />, {
    role: "MGR",
    sessionMode: "live",
    path: ROUTES.pharmacyPurchasing,
  });
  const form = await screen.findByRole("region", {
    name: "ยืนยันใบสั่งซื้อ",
  });
  await user.type(within(form).getByLabelText("เลขที่ใบสั่งซื้อ"), "PO-1");
  await user.click(within(form).getByRole("button", { name: "ยืนยันใบสั่งซื้อ" }));

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toEqual({
    operation_key: expect.any(String),
    purchase_order: "PO-1",
  });
  expect(await screen.findByText("ยืนยันใบสั่งซื้อแล้ว")).toBeTruthy();
});

it("receives against the purchase order with one acknowledgement", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(RECEIVE, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "received",
          movement: {
            kind: "purchase_receipt",
            purchase_order: "PO-1",
            item_code: "ITEM-1",
            warehouse: "WH-1",
            qty: 2,
            batch: null,
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
  const form = await screen.findByRole("region", {
    name: "รับสินค้าตามใบสั่งซื้อ (เซิร์ฟเวอร์)",
  });
  await user.type(within(form).getByLabelText("เลขที่ใบสั่งซื้อ"), "PO-1");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนรับ"), "2");
  await user.type(within(form).getByLabelText("คลังรับเข้า"), "WH-1");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันรับตามใบสั่งซื้อ" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    purchase_order: "PO-1",
    item_code: "ITEM-1",
    qty: 2,
    warehouse: "WH-1",
  });
  expect(await screen.findByText("รับสินค้าตามใบสั่งซื้อแล้ว")).toBeTruthy();
  expect(within(form).queryByText(/PR-/)).toBeNull();
});

it("surfaces a server refusal without consuming a second attempt", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  server.use(
    http.post(RECEIVE, () =>
      HttpResponse.json({ message: { code: "VALIDATION" } }, { status: 417 }),
    ),
  );
  renderWithFixtures(<PharmacyReceivePage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyReceive,
  });
  const form = await screen.findByRole("region", {
    name: "รับสินค้าตามใบสั่งซื้อ (เซิร์ฟเวอร์)",
  });
  await user.type(within(form).getByLabelText("เลขที่ใบสั่งซื้อ"), "PO-1");
  await user.type(within(form).getByLabelText("รหัสสินค้า"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวนรับ"), "2");
  await user.type(within(form).getByLabelText("คลังรับเข้า"), "WH-1");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันรับตามใบสั่งซื้อ" }),
  );

  expect(
    await screen.findByText(
      /เซิร์ฟเวอร์ปฏิเสธรายการนี้ ตรวจใบสั่งซื้อ รายการ คลัง จำนวน หรือล็อต/,
    ),
  ).toBeTruthy();
});
