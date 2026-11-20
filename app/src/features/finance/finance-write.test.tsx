import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { InvoiceSubmitForm, PaymentReceiveForm } from "./finance-write";
import { InvoicesPage } from "./InvoicesPage";

const PREVIEW = "/api/method/clinic_ui.api.finance_write.save_invoice_draft";
const SUBMIT = "/api/method/clinic_ui.api.finance_write.submit_invoice";
const RECEIVE = "/api/method/clinic_ui.api.finance_write.receive_payment";
const ACK = "/api/method/clinic_ui.api.finance_write.ack";
const POSTED = "/api/resource/Sales%20Invoice";
const TOKEN = "a".repeat(64);

const ENABLED = {
  "write.invoice.submit": "enabled",
  "write.payment.receive": "enabled",
} as const;

function serve(caps: Record<string, string>, role: "CAS" = "CAS") {
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, caps)));
}

function ack() {
  return HttpResponse.json({ message: { acknowledged: true } });
}

function RefreshSession() {
  const { refresh } = useSession();
  return <button type="button" onClick={() => void refresh()}>Refresh session</button>;
}

function previewMessage(available = false) {
  return {
    status: "preview",
    customer: "CUST-1",
    currency: "USD",
    company: "SIT Synthetic Company USD",
    selling_price_list: "Standard Selling",
    due_date: "2026-11-30",
    lines: [{ item_code: "ITEM-1", qty: 2, rate: 10, amount: 20 }],
    net_total: 20,
    discount: 0,
    discount_available: available,
    total_taxes: 0,
    total: 20,
  };
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

async function fillInvoice(
  user: ReturnType<typeof userEvent.setup>,
  form: HTMLElement,
) {
  await user.type(within(form).getByLabelText("ลูกค้าบัญชี (ต้องมีอยู่แล้ว)"), "CUST-1");
  await user.type(within(form).getByLabelText("ราคาที่ขาย"), "Standard Selling");
  await user.type(
    within(form).getByLabelText("วันครบกำหนด (ระบุเอง)"),
    "2026-11-30",
  );
  await user.type(within(form).getByLabelText("รหัสสินค้า/บริการ"), "ITEM-1");
  await user.type(within(form).getByLabelText("จำนวน"), "2");
}

it("hides both finance forms while the capabilities are read_only", async () => {
  serve({ "read.patient": "enabled" });
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  await screen.findByTestId("invoices-page");
  expect(screen.queryByTestId("invoice-write-form")).toBeNull();
});

it("offers native invoice lookup without write capability and handles list, detail and denied reads", async () => {
  const user = userEvent.setup();
  serve({ "read.patient": "enabled" });
  server.use(
    http.get("/api/method/clinic_ui.api.finance_write.invoices", ({ request }) => {
      expect(new URL(request.url).searchParams.get("customer")).toBe("CUST & 1");
      return HttpResponse.json({ message: { items: [{ invoice_id: "SINV-1", status: "Unpaid", docstatus: 1 }] } });
    }),
    http.get("/api/method/clinic_ui.api.finance_write.view", ({ request }) => {
      const id = new URL(request.url).searchParams.get("invoice_id");
      return id === "SINV-1"
        ? HttpResponse.json({ message: { invoice: { invoice_id: id, status: "Unpaid", docstatus: 1 } } })
        : new HttpResponse(null, { status: 403 });
    }),
  );
  renderWithFixtures(<InvoicesPage />, { role: "CAS", sessionMode: "live", path: ROUTES.invoices });
  const lookup = await screen.findByRole("region", { name: "ค้นหาใบแจ้งหนี้บนเซิร์ฟเวอร์" });
  await user.type(within(lookup).getByLabelText("ลูกค้าสำหรับค้นหา"), "CUST & 1");
  await user.click(within(lookup).getByRole("button", { name: "ค้นหาใบแจ้งหนี้ของลูกค้า" }));
  expect(await within(lookup).findByText(/SINV-1 · Unpaid/)).toBeInTheDocument();
  await user.type(within(lookup).getByLabelText("เลขใบแจ้งหนี้สำหรับค้นหา"), "SINV-1");
  await user.click(within(lookup).getByRole("button", { name: "ค้นหาตามเลขใบแจ้งหนี้" }));
  expect(await within(lookup).findByText(/SINV-1 · Unpaid/)).toBeInTheDocument();
  await user.clear(within(lookup).getByLabelText("เลขใบแจ้งหนี้สำหรับค้นหา"));
  await user.type(within(lookup).getByLabelText("เลขใบแจ้งหนี้สำหรับค้นหา"), "missing");
  await user.click(within(lookup).getByRole("button", { name: "ค้นหาตามเลขใบแจ้งหนี้" }));
  expect(await within(lookup).findByRole("alert")).toBeInTheDocument();
  expect(within(lookup).queryByText(/SINV-1 · Unpaid/)).toBeNull();
});

it("reads only the selected submitted invoice and shows its stored figures", async () => {
  const user = userEvent.setup();
  const requested: string[] = [];
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  serve({ "read.patient": "enabled" });
  server.use(
    http.get("/api/method/clinic_ui.api.finance_write.invoices", () =>
      HttpResponse.json({ message: { items: [
        { invoice_id: "SINV-1", status: "Unpaid", docstatus: 1 },
        { invoice_id: "SINV-2", status: "Unpaid", docstatus: 1 },
        { invoice_id: "DRAFT-1", status: "Draft", docstatus: 0 },
      ] } }),
    ),
    http.get(`${POSTED}/:id`, async ({ params }) => {
      requested.push(String(params.id));
      if (params.id === "SINV-2") return new HttpResponse(null, { status: 403 });
      await waiting;
      return HttpResponse.json({ data: {
        name: params.id, docstatus: 1, status: "Unpaid", customer: "CUST-1",
        company: "Synthetic Company", posting_date: "2026-11-20", due_date: "2026-11-30",
        currency: "USD", total: 129.25, discount_amount: 9.21, net_total: 120.04,
        total_taxes_and_charges: 8.4, grand_total: 128.44, rounded_total: 128.5,
        outstanding_amount: 98.44,
        items: [{
          item_code: "ITEM-1", item_name: "Stored service", qty: 3,
          uom: "Session", rate: 42.73, amount: 128.19, net_amount: 115.87,
        }],
        taxes: [{ description: "Stored tax", rate: 7, tax_amount: 8.4 }],
      } });
    }),
  );
  renderWithFixtures(<><RefreshSession /><InvoiceSubmitForm /></>, { role: "CAS", sessionMode: "live" });
  await user.type(await screen.findByLabelText("ลูกค้าสำหรับค้นหา"), "CUST-1");
  await user.click(screen.getByRole("button", { name: "ค้นหาใบแจ้งหนี้ของลูกค้า" }));
  const first = await screen.findByRole("button", { name: "ดูรายละเอียดใบแจ้งหนี้ SINV-1" });
  const second = screen.getByRole("button", { name: "ดูรายละเอียดใบแจ้งหนี้ SINV-2" });
  expect(screen.queryByRole("button", { name: /ดูรายละเอียดใบแจ้งหนี้ DRAFT-1/ })).toBeNull();
  expect(requested).toEqual([]);

  await user.click(first);
  expect(screen.getByText("กำลังโหลดรายละเอียดใบแจ้งหนี้…")).toBeInTheDocument();
  release();
  const detail = await screen.findByRole("region", { name: "รายละเอียดใบแจ้งหนี้ SINV-1" });
  expect(within(detail).getByRole("row", { name: /Stored service.*3.*Session.*42\.73.*128\.19.*115\.87/ })).toBeInTheDocument();
  expect(within(detail).getByRole("row", { name: /Stored tax.*7.*8\.4/ })).toBeInTheDocument();
  for (const stored of ["Unpaid", "USD", /129\.25/, /9\.21/, /120\.04/, /128\.44/, /128\.50/, /98\.44/]) {
    expect(within(detail).getByText(stored)).toBeInTheDocument();
  }
  expect(within(detail).getByText("ยอดรวมก่อนปัดเศษ").nextElementSibling).toHaveTextContent("128.44");
  expect(within(detail).getByText("ยอดรวมหลังปัดเศษ").nextElementSibling).toHaveTextContent("128.50");

  await user.click(second);
  expect(screen.queryByText(/Stored service/)).toBeNull();
  expect(await screen.findByRole("alert")).toHaveTextContent("ไม่มีสิทธิ์ดูรายละเอียดใบแจ้งหนี้");
  expect(requested).toEqual(["SINV-1", "SINV-2"]);

  await user.click(first);
  expect(await screen.findByText(/Stored service/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh session" }));
  await screen.findByRole("region", { name: "ค้นหาใบแจ้งหนี้บนเซิร์ฟเวอร์" });
  expect(screen.queryByText(/Stored service|SINV-1 · Unpaid/)).toBeNull();
  expect(screen.queryByRole("button", { name: "ดูรายละเอียดใบแจ้งหนี้ SINV-1" })).toBeNull();
});

it("shows unavailable details without falling back to a sample", async () => {
  const user = userEvent.setup();
  serve({ "read.patient": "enabled" });
  server.use(
    http.get("/api/method/clinic_ui.api.finance_write.view", () =>
      HttpResponse.json({ message: { invoice: { invoice_id: "SINV-MISSING", status: "Unpaid", docstatus: 1 } } }),
    ),
    http.get(`${POSTED}/:id`, () => new HttpResponse(null, { status: 404 })),
  );
  renderWithFixtures(<InvoiceSubmitForm />, { role: "CAS", sessionMode: "live" });
  await user.type(await screen.findByLabelText("เลขใบแจ้งหนี้สำหรับค้นหา"), "SINV-MISSING");
  await user.click(screen.getByRole("button", { name: "ค้นหาตามเลขใบแจ้งหนี้" }));
  await user.click(await screen.findByRole("button", { name: "ดูรายละเอียดใบแจ้งหนี้ SINV-MISSING" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ไม่สามารถโหลดรายละเอียดใบแจ้งหนี้ได้");
  expect(screen.queryByRole("region", { name: "รายละเอียดใบแจ้งหนี้ SINV-MISSING" })).toBeNull();
});

it.each(["doctor", "guest", "fixture"] as const)("keeps posted invoice details out of %s sessions", async (context) => {
  const requested = vi.fn();
  server.use(http.get(`${POSTED}/:id`, requested));
  if (context === "fixture") vi.stubEnv("VITE_CLINIC_NATIVE", "0");
  server.use(http.get(NATIVE_BOOTSTRAP, () =>
    context === "guest"
      ? new HttpResponse(null, { status: 401 })
      : nativeSession(context === "doctor" ? "DOC" : "CAS", ENABLED),
  ));
  renderWithFixtures(<InvoiceSubmitForm />, {
    role: context === "doctor" ? "DOC" : "CAS",
    sessionMode: context === "fixture" ? "fixture" : "live",
  });
  if (context === "doctor") await screen.findByTestId("invoice-write-form");
  expect(screen.queryByRole("region", { name: "ค้นหาใบแจ้งหนี้บนเซิร์ฟเวอร์" })).toBeNull();
  expect(screen.queryByRole("button", { name: /ดูรายละเอียดใบแจ้งหนี้/ })).toBeNull();
  expect(requested).not.toHaveBeenCalled();
});

it("previews the server totals and then submits once without acknowledging", async () => {
  const user = userEvent.setup();
  const previews: Record<string, unknown>[] = [];
  const bodies: Record<string, unknown>[] = [];
  let acknowledgements = 0;
  serve(ENABLED);
  server.use(
    http.post(PREVIEW, async ({ request }) => {
      previews.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ message: previewMessage() });
    }),
    http.post(SUBMIT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: { status: "submitted", invoice: "SINV-1", total: 20 },
      });
    }),
    http.post(ACK, () => {
      acknowledgements += 1;
      return ack();
    }),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);

  await user.click(
    within(form).getByRole("button", { name: "ตรวจทานยอดจากเซิร์ฟเวอร์" }),
  );
  const preview = await screen.findByTestId("invoice-preview");
  expect(within(preview).getAllByText("20.00").length).toBeGreaterThan(0);
  expect(previews[0]).toMatchObject({
    customer: "CUST-1",
    selling_price_list: "Standard Selling",
    due_date: "2026-11-30",
    items: [{ item_code: "ITEM-1", qty: 2 }],
  });
  expect(previews[0]).not.toHaveProperty("rate");

  await user.click(
    within(form).getByRole("button", { name: "ยืนยันออกใบแจ้งหนี้" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    customer: "CUST-1",
    items: [{ item_code: "ITEM-1", qty: 2 }],
  });
  expect(bodies[0]).not.toHaveProperty("rate");
  expect(bodies[0]).not.toHaveProperty("grand_total");
  expect(await screen.findByText(/ยอดที่โพสต์ 20\.00/)).toBeTruthy();
  expect(acknowledgements).toBe(0);
});

it("offers the discount only when the server advertises the path", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  server.use(
    http.post(PREVIEW, () =>
      HttpResponse.json({ message: previewMessage(false) }),
    ),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);
  await user.click(
    within(form).getByRole("button", { name: "ตรวจทานยอดจากเซิร์ฟเวอร์" }),
  );
  await screen.findByTestId("invoice-preview");
  expect(within(form).queryByLabelText("ส่วนลด (จำนวนเงิน)")).toBeNull();
});

it("shows the discount field and sends an absolute amount when available", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(PREVIEW, () =>
      HttpResponse.json({ message: previewMessage(true) }),
    ),
    http.post(SUBMIT, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: { status: "submitted", invoice: "SINV-1", total: 18 },
      });
    }),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);
  await user.click(
    within(form).getByRole("button", { name: "ตรวจทานยอดจากเซิร์ฟเวอร์" }),
  );
  const discount = await within(form).findByLabelText("ส่วนลด (จำนวนเงิน)");
  await user.type(discount, "2");
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันออกใบแจ้งหนี้" }),
  );

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]?.discount).toBe(2);
  expect(bodies[0]).not.toHaveProperty("discount_percentage");
});

it("surfaces a server refusal without posting twice", async () => {
  const user = userEvent.setup();
  let calls = 0;
  serve(ENABLED);
  server.use(
    http.post(SUBMIT, () => {
      calls += 1;
      return HttpResponse.json(
        { message: { code: "VALIDATION", problem: "discount" } },
        { status: 417 },
      );
    }),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันออกใบแจ้งหนี้" }),
  );

  expect(await screen.findByText(/เซิร์ฟเวอร์ปฏิเสธรายการนี้/)).toBeTruthy();
  expect(calls).toBe(1);
  expect(
    within(form).getByRole("button", { name: "ยืนยันออกใบแจ้งหนี้" }),
  ).toBeEnabled();
});

it("stops the mounted form after an uncertain submit", async () => {
  const user = userEvent.setup();
  let calls = 0;
  serve(ENABLED);
  server.use(
    http.post(SUBMIT, () => {
      calls += 1;
      return new HttpResponse(null, { status: 500 });
    }),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);
  const submitButton = within(form).getByRole("button", {
    name: "ยืนยันออกใบแจ้งหนี้",
  });
  await user.click(submitButton);

  expect(await screen.findByText(/ผลลัพธ์ไม่แน่นอน/)).toBeTruthy();
  expect(submitButton).toBeDisabled();
  expect(
    within(form).getByRole("button", { name: "ตรวจทานยอดจากเซิร์ฟเวอร์" }),
  ).toBeDisabled();
  expect(within(form).getByLabelText("จำนวน")).toBeDisabled();
  expect(calls).toBe(1);
});

it("rejects a legacy ackToken envelope and stops the form", async () => {
  const user = userEvent.setup();
  let calls = 0;
  let acknowledgements = 0;
  serve(ENABLED);
  server.use(
    http.post(SUBMIT, () => {
      calls += 1;
      return HttpResponse.json({
        message: {
          status: "submitted",
          invoice: "SINV-1",
          total: 20,
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, () => {
      acknowledgements += 1;
      return ack();
    }),
  );
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  const form = await screen.findByTestId("invoice-write-form");
  await fillInvoice(user, form);
  await user.click(
    within(form).getByRole("button", { name: "ยืนยันออกใบแจ้งหนี้" }),
  );

  expect(await screen.findByText(/ผลลัพธ์ไม่แน่นอน/)).toBeTruthy();
  expect(acknowledgements).toBe(0);
  expect(calls).toBe(1);
});

it("keeps the void/amend control not available", async () => {
  serve(ENABLED);
  renderWithFixtures(<InvoicesPage />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.invoices,
  });
  await screen.findByTestId("invoices-page");
  expect(
    screen.queryByRole("button", { name: /ยกเลิกใบแจ้งหนี้|Void invoice/ }),
  ).toBeNull();
});

// The legacy writer contract is tested in isolation; the native Payments route never mounts it.
it("receives a payment with one allocation equal to the paid amount", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(RECEIVE, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "received",
          payment: "PE-1",
          invoice: "SINV-1",
          outstanding: 0,
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PaymentReceiveForm />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.payments,
  });
  const form = await screen.findByTestId("payment-write-form");
  await user.type(within(form).getByLabelText("เลขที่ใบแจ้งหนี้"), "SINV-1");
  await user.type(within(form).getByLabelText("ยอดรับชำระ"), "20");
  await user.click(within(form).getByRole("button", { name: "ยืนยันรับชำระ" }));

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    invoice: "SINV-1",
    mode_of_payment: "Cash",
    amount: 20,
    allocations: [{ invoice: "SINV-1", amount: 20 }],
  });
  expect(JSON.stringify(bodies[0])).not.toMatch(/card_number|cvv|pan/i);
  expect(await within(form).findByRole("status")).toHaveTextContent(/PE-1.*ยอดค้างคงเหลือ 0\.00/);
});

it("requires a terminal reference for a card payment and sends it", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED);
  server.use(
    http.post(RECEIVE, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "received",
          payment: "PE-1",
          invoice: "SINV-1",
          outstanding: 15,
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<PaymentReceiveForm />, {
    role: "CAS",
    sessionMode: "live",
    path: ROUTES.payments,
  });
  const form = await screen.findByTestId("payment-write-form");
  await user.type(within(form).getByLabelText("เลขที่ใบแจ้งหนี้"), "SINV-1");
  await user.selectOptions(
    within(form).getByLabelText("วิธีชำระ"),
    "Credit Card",
  );
  await user.type(within(form).getByLabelText("ยอดรับชำระ"), "5");
  await user.click(within(form).getByRole("button", { name: "ยืนยันรับชำระ" }));
  expect(
    await screen.findByText(/ตรวจเลขที่ใบแจ้งหนี้ ยอดรับชำระ และเลขอ้างอิงบัตร/),
  ).toBeTruthy();
  expect(bodies).toHaveLength(0);

  await user.type(
    within(form).getByLabelText("เลขอ้างอิงเครื่องรูดบัตร"),
    "TERM-001",
  );
  await user.click(within(form).getByRole("button", { name: "ยืนยันรับชำระ" }));

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({
    mode_of_payment: "Credit Card",
    reference_no: "TERM-001",
    amount: 5,
    allocations: [{ invoice: "SINV-1", amount: 5 }],
  });
  const confirmation = await within(form).findByRole("status");
  expect(confirmation).toHaveTextContent(/PE-1.*ยอดค้างคงเหลือ 15\.00/);
  expect(confirmation).not.toHaveTextContent(/receipt|paid in full|ใบเสร็จ|ชำระครบ/i);
});

it("keeps an unacknowledged payment uncertain without showing a payment ID or balance", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  server.use(
    http.post(RECEIVE, () => HttpResponse.json({ message: {
      status: "received", payment: "PE-UNCERTAIN", invoice: "SINV-1", outstanding: 15, ackToken: TOKEN,
    } })),
    http.post(ACK, () => new HttpResponse(null, { status: 503 })),
  );
  renderWithFixtures(<PaymentReceiveForm />, { role: "CAS", sessionMode: "live", path: ROUTES.payments });
  const form = await screen.findByTestId("payment-write-form");
  await user.type(within(form).getByLabelText("เลขที่ใบแจ้งหนี้"), "SINV-1");
  await user.type(within(form).getByLabelText("ยอดรับชำระ"), "5");
  await user.click(within(form).getByRole("button", { name: "ยืนยันรับชำระ" }));
  expect(await within(form).findByRole("alert")).toHaveTextContent(/ผลลัพธ์ไม่แน่นอน/);
  expect(within(form).queryByRole("status")).toBeNull();
  expect(within(form).queryByText(/PE-UNCERTAIN|ยอดค้างคงเหลือ 15/)).toBeNull();
});
