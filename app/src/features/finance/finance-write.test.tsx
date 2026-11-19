import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { InvoicesPage } from "./InvoicesPage";
import { PaymentsPage } from "./PaymentsPage";

const PREVIEW = "/api/method/clinic_ui.api.finance_write.save_invoice_draft";
const SUBMIT = "/api/method/clinic_ui.api.finance_write.submit_invoice";
const RECEIVE = "/api/method/clinic_ui.api.finance_write.receive_payment";
const ACK = "/api/method/clinic_ui.api.finance_write.ack";
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
  renderWithFixtures(<PaymentsPage />, {
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
  expect(await screen.findByText(/ยอดค้างคงเหลือ 0\.00/)).toBeTruthy();
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
  renderWithFixtures(<PaymentsPage />, {
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
});
