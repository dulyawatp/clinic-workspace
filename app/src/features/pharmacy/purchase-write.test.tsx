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

it("allows manual recovery when draft writes are disabled, with server permission authoritative", async () => {
  const user = userEvent.setup();
  serve({ "read.patient": "enabled" });
  let reads = 0;
  server.use(http.get("/api/method/clinic_ui.api.purchase_write.drafts", ({ request }) => {
    expect(new URL(request.url).searchParams.get("supplier")).toBe("SUP-1");
    reads += 1;
    return reads === 1
      ? HttpResponse.json({ message: { items: [{ purchase_order_id: "PO-1", status: "Draft", docstatus: 0 }] } })
      : new HttpResponse(null, { status: 403 });
  }));
  renderWithFixtures(<PharmacyPurchasingPage />, {
    role: "PHA",
    sessionMode: "live",
    path: ROUTES.pharmacyPurchasing,
  });
  const recovery = await screen.findByRole("region", { name: "ตรวจสอบใบสั่งซื้อ" });
  expect(screen.queryByTestId("purchase-write-form")).toBeNull();
  await user.type(within(recovery).getByLabelText("ผู้จำหน่าย"), "SUP-1");
  await user.click(within(recovery).getByRole("button", { name: "ค้นหาฉบับร่างของผู้จำหน่าย" }));
  expect(await within(recovery).findByText(/PO-1.*Draft/)).toBeTruthy();
  await user.click(within(recovery).getByRole("button", { name: "ค้นหาฉบับร่างของผู้จำหน่าย" }));
  expect(await within(recovery).findByRole("alert")).toHaveTextContent("โหลดใบสั่งซื้อไม่ได้");
  expect(within(recovery).queryByText(/PO-1.*Draft/)).toBeNull();
});

it("drafts one purchase order with itemized lines and no docstatus", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  // No acknowledgement handler is registered: any second request would fail
  // the suite through the unhandled-frame guard.
  server.use(
    http.post(DRAFT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "created",
          purchase_order: {
            purchase_order_id: "PO-DRAFT-0001",
            status: "Draft",
            docstatus: 0,
          },
        },
      });
    }),
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
  expect(
    await screen.findByText("สร้างใบสั่งซื้อแล้ว · เลขที่ PO-DRAFT-0001"),
  ).toBeTruthy();
  expect(bodies).toHaveLength(1);
  expect(localStorage.length).toBe(0);
});

it("shows inspect-records guidance for an uncertain draft and never acknowledges", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  let acknowledgements = 0;
  serve(ENABLED);
  server.use(
    http.post(ACK, () => {
      acknowledgements += 1;
      return ack();
    }),
    http.post(DRAFT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(
        { message: { code: "UPSTREAM" } },
        { status: 503 },
      );
    }),
    http.get("/api/method/clinic_ui.api.purchase_write.drafts", ({ request }) => {
      expect(new URL(request.url).searchParams.get("supplier")).toBe("SUP-1");
      return HttpResponse.json({ message: { items: [{ purchase_order_id: "PO-OLD", status: "Draft", docstatus: 0 }] } });
    }),
    http.get("/api/method/clinic_ui.api.purchase_write.view", ({ request }) => {
      expect(new URL(request.url).searchParams.get("purchase_order_id")).toBe("PO-OLD");
      return HttpResponse.json({ message: { purchase_order: { purchase_order_id: "PO-OLD", status: "Draft", docstatus: 0 } } });
    }),
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

  expect(
    await screen.findByText(/ตรวจสอบรายการใบสั่งซื้อในระบบก่อนสร้างใหม่/),
  ).toBeTruthy();
  const submit = within(form).getByRole("button", { name: "ยืนยันสร้างใบสั่งซื้อ" });
  expect(submit).toBeDisabled();
  await user.click(submit);
  await user.click(screen.getByRole("button", { name: "ค้นหาฉบับร่างของผู้จำหน่าย" }));
  expect(await screen.findByText(/PO-OLD.*Draft/)).toBeTruthy();
  await user.type(screen.getByLabelText("รหัสใบสั่งซื้อ"), "PO-OLD");
  await user.click(screen.getByRole("button", { name: "ดูสถานะใบสั่งซื้อ" }));
  expect(await screen.findByText(/PO-OLD.*Draft/)).toBeTruthy();
  expect(bodies).toHaveLength(1);
  expect(acknowledgements).toBe(0);
  expect(localStorage.length).toBe(0);
});

it("shows a known refusal for a rejected draft instead of the uncertain copy", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(DRAFT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(
        { message: { code: "VALIDATION" } },
        { status: 422 },
      );
    }),
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

  expect(
    await screen.findByText(/เซิร์ฟเวอร์ปฏิเสธรายการนี้/),
  ).toBeTruthy();
  expect(screen.queryByText(/ตรวจสอบรายการใบสั่งซื้อ/)).toBeNull();
  expect(bodies).toHaveLength(1);
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
