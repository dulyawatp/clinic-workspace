import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";
import * as financeWrite from "../../transport/native/finance-write";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { PaymentsPage } from "./PaymentsPage";

const { list, ReadError } = vi.hoisted(() => ({
  list: vi.fn(),
  ReadError: class extends Error {
    constructor(public kind: "permission" | "unavailable") {
      super(kind);
    }
  },
}));

const SALES_INVOICE = "/api/resource/Sales%20Invoice";
const RECEIVE = "/api/method/clinic_ui.api.finance_write.receive_payment";
const ACK = "/api/method/clinic_ui.api.finance_write.ack";
const ACK_TOKEN = "a".repeat(64);
const PAYMENT_LOCK = "clinic.finance-write.pending.payment_receive";
const PAYMENT_ID = "PE-TEST-1";
const PAYMENT_ENTRY = `/api/resource/Payment%20Entry/${PAYMENT_ID}`;
const RECEIPT_NAME = "Open receipt (new tab)";

vi.mock("../../transport/native/payable-invoice", () => ({
  listNativePayableInvoices: list,
  PayableInvoiceReadError: ReadError,
  PAYABLE_INVOICE_PAGE_SIZE: 25,
}));

const thb = {
  id: "ACC-SINV-2026-00143",
  customer: "CUST-00814",
  customerName: "Arun Health Benefits",
  postingDate: "2026-11-18",
  currency: "THB",
  total: 3500,
  outstanding: 2850.5,
};
const usd = {
  id: "ACC-SINV-2026-00146",
  customer: "CUST-00853",
  customerName: "Pacific Visitor Care",
  postingDate: "2026-11-20",
  currency: "USD",
  total: 160,
  outstanding: 85.5,
};
const anotherThb = {
  ...thb,
  id: "ACC-SINV-2026-00145",
  customer: "CUST-00824",
  customerName: "Siam Family Cover",
};

function RefreshSession() {
  const { refresh } = useSession();
  return <button type="button" onClick={() => void refresh()}>Refresh session</button>;
}

function open() {
  return renderWithDataRouter(
    [{ path: ROUTES.payments, element: <><RefreshSession /><PaymentsPage /></> }],
    { initialEntries: [ROUTES.payments], role: "CAS" },
  );
}

const currency = () => screen.getByRole("combobox", { name: "Currency" });
const selectInvoice = (id: string) =>
  screen.findByRole("button", { name: `Select ${id}` });
const review = () => within(screen.getByRole("region", { name: "Payment review" }));
const confirm = () => review().getByRole("button", { name: "Confirm full payment" });

function optIn({
  marker = "full_balance_v1",
  capability = "enabled",
  role = "CAS",
  sessionEpoch = "native-epoch",
}: { marker?: unknown; capability?: unknown; role?: string; sessionEpoch?: string } = {}) {
  server.use(http.get(NATIVE_BOOTSTRAP, () => HttpResponse.json({
    message: {
      mode: "live", sessionEpoch,
      user: { id: "native|cas", displayName: "Native CAS", roles: [role] },
      capabilities: { "write.payment.receive": capability },
      csrfToken: "native-csrf",
      paymentSettlement: marker,
    },
  })));
}

function detail(overrides: Record<string, unknown> = {}) {
  return HttpResponse.json({ data: {
    name: thb.id, docstatus: 1, status: "Unpaid", customer: thb.customer, company: "Clinic",
    posting_date: thb.postingDate, currency: thb.currency, total: thb.total, discount_amount: 0,
    net_total: thb.total, total_taxes_and_charges: 0, grand_total: thb.total,
    outstanding_amount: thb.outstanding,
    items: [{ item_name: "Consultation", qty: 1, uom: "Nos", rate: thb.total, amount: thb.total, net_amount: thb.total }],
    taxes: [],
    ...overrides,
  } });
}

function received() {
  return HttpResponse.json({
    message: { payment: PAYMENT_ID, invoice: thb.id, status: "received", outstanding: 0, ackToken: ACK_TOKEN },
  });
}

const allocation = { reference_doctype: "Sales Invoice", reference_name: thb.id, allocated_amount: thb.outstanding };

function receipt(mode = "Cash", overrides: Record<string, unknown> = {}) {
  return HttpResponse.json({ data: {
    name: PAYMENT_ID,
    docstatus: 1,
    payment_type: "Receive",
    mode_of_payment: mode,
    paid_from_account_currency: thb.currency,
    paid_to_account_currency: thb.currency,
    paid_amount: thb.outstanding,
    received_amount: thb.outstanding,
    references: [allocation],
    ...overrides,
  } });
}

function setupReceiptFlow() {
  optIn();
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  server.use(
    http.get(`${SALES_INVOICE}/${thb.id}`, () => detail()),
    http.post(RECEIVE, received),
    http.post(ACK, () => HttpResponse.json({ message: { acknowledged: true } })),
  );
}

beforeEach(() => {
  localStorage.setItem(LOCALE_KEY, "en");
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("CAS", {
    "write.payment.receive": "enabled",
  })));
  list.mockReset();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  clearNativeCsrfToken();
  localStorage.removeItem(LOCALE_KEY);
  localStorage.removeItem(PAYMENT_LOCK);
});

it("keeps the fixture payment route local without a Sales Invoice read or a write", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "0");
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  open();

  expect(await screen.findByRole("table", { name: "Linked posted payments & receipts" })).toBeVisible();
  expect(screen.getByText("Demo data", { exact: true })).toBeVisible();
  expect(list).not.toHaveBeenCalled();
  expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"]))
    .toEqual([["/api/session", "GET"]]);
});

it("makes only bootstrap and a Sales Invoice GET on the native CAS route", async () => {
  const { listNativePayableInvoices } = await vi.importActual<
    typeof import("../../transport/native/payable-invoice")
  >("../../transport/native/payable-invoice");
  list.mockImplementationOnce(listNativePayableInvoices);
  server.use(http.get(SALES_INVOICE, () => HttpResponse.json({ data: [{
    name: thb.id,
    customer: thb.customer,
    customer_name: thb.customerName,
    posting_date: thb.postingDate,
    currency: thb.currency,
    grand_total: thb.total,
    outstanding_amount: thb.outstanding,
    docstatus: 1,
  }] })));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();

  await user.click(await selectInvoice(thb.id));
  const confirm = review().getByRole("button", { name: "Confirm full payment" });
  expect(confirm).toBeDisabled();
  await user.click(confirm);
  expect(fetchSpy.mock.calls.map(([url, init]) => [String(url).split("?")[0], init?.method ?? "GET"]))
    .toEqual([[NATIVE_BOOTSTRAP, "GET"], [SALES_INVOICE, "GET"]]);
});

it.each([
  { marker: "full_balance_v2" },
  { marker: "full_balance_v1 " },
  { marker: true },
  { capability: "read_only" },
  { capability: null },
])("keeps payment read-only unless both exact signals are present (%j)", async (signal) => {
  optIn(signal);
  list.mockResolvedValue({ rows: [thb], hasMore: false });
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  expect(confirm()).toBeDisabled();
  await user.click(confirm());
  expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([NATIVE_BOOTSTRAP]);
});

it.each(["Cash", "Credit Card"])("rechecks and posts exactly one acknowledged full %s settlement", async (mode) => {
  optIn();
  list.mockResolvedValue({ rows: [thb], hasMore: false });
  const calls: string[] = [];
  const writes: Record<string, unknown>[] = [];
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  let releaseAck!: () => void;
  const delayedAck = new Promise<void>((resolve) => { releaseAck = resolve; });
  server.use(
    http.get(`${SALES_INVOICE}/${thb.id}`, async () => {
      calls.push("detail");
      await delayed;
      return detail();
    }),
    http.post(RECEIVE, async ({ request }) => {
      calls.push("receive");
      writes.push(await request.json() as Record<string, unknown>);
      return received();
    }),
    http.post(ACK, async ({ request }) => {
      calls.push("ack");
      writes.push(await request.json() as Record<string, unknown>);
      await delayedAck;
      return HttpResponse.json({ message: { acknowledged: true } });
    }),
    http.get(PAYMENT_ENTRY, () => {
      calls.push("receipt");
      return receipt(mode);
    }),
  );
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  if (mode === "Credit Card") {
    await user.selectOptions(review().getByLabelText("Payment method"), mode);
    expect(confirm()).toBeDisabled();
    await user.type(review().getByLabelText("Card terminal reference"), "TERM-492218");
  }
  expect(confirm()).toBeEnabled();
  const form = confirm().closest("form")!;
  act(() => {
    fireEvent.submit(form);
    fireEvent.submit(form);
  });
  await waitFor(() => expect(calls).toEqual(["detail"]));
  await act(async () => release());
  await waitFor(() => expect(calls).toEqual(["detail", "receive", "ack"]));
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
  await act(async () => releaseAck());
  expect(await review().findByText("Payment received. Payment ID: PE-TEST-1.")).toHaveAttribute("role", "status");
  const print = await review().findByRole("link", { name: RECEIPT_NAME });
  expect(print).toHaveAttribute("href", `/printview?doctype=Payment+Entry&name=${PAYMENT_ID}&format=Standard&no_letterhead=1`);
  expect(print).toHaveAttribute("target", "_blank");
  expect(print).toHaveAttribute("rel", "noopener noreferrer");
  expect(calls).toEqual(["detail", "receive", "ack", "receipt"]);
  expect(writes).toEqual([
    {
      operation_key: expect.any(String),
      invoice: thb.id,
      mode_of_payment: mode,
      amount: thb.outstanding,
      allocations: [{ invoice: thb.id, amount: thb.outstanding }],
      ...(mode === "Credit Card" ? { reference_no: "TERM-492218" } : {}),
    },
    { operation_key: writes[0]?.operation_key, kind: "payment_receive", ack_token: ACK_TOKEN },
  ]);
  expect(confirm()).toBeDisabled();
});

it.each([
  { name: "PE-other" },
  { docstatus: 0 },
  { docstatus: 2 },
  { payment_type: "Pay" },
  { mode_of_payment: "Credit Card" },
  { paid_from_account_currency: "USD" },
  { paid_to_account_currency: "USD" },
  { paid_amount: thb.outstanding + 0.01 },
  { received_amount: String(thb.outstanding) },
  { references: [] },
  { references: [allocation, allocation] },
  { references: [{ ...allocation, reference_doctype: "Purchase Invoice" }] },
  { references: [{ ...allocation, reference_name: anotherThb.id }] },
  { references: [{ ...allocation, allocated_amount: thb.outstanding - 0.01 }] },
])("preserves confirmation but refuses a mismatched or malformed receipt (%j)", async (overrides) => {
  setupReceiptFlow();
  server.use(http.get(PAYMENT_ENTRY, () => receipt("Cash", overrides)));
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  expect(await review().findByText("Receipt unavailable.")).toBeVisible();
  expect(review().getByText("Payment received. Payment ID: PE-TEST-1.")).toBeVisible();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
  expect(confirm()).toBeDisabled();
});

it("keeps a denied receipt distinct from confirmed payment and does not read it again or post again", async () => {
  setupReceiptFlow();
  const getReceipt = vi.fn(() => new HttpResponse(null, { status: 403 }));
  server.use(http.get(PAYMENT_ENTRY, getReceipt));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  expect(await review().findByText("Receipt unavailable.")).toBeVisible();
  expect(review().getByText("Payment received. Payment ID: PE-TEST-1.")).toBeVisible();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
  await user.click(confirm());
  expect(getReceipt).toHaveBeenCalledTimes(1);
  expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
    [NATIVE_BOOTSTRAP, "GET"],
    [`${SALES_INVOICE}/${thb.id}`, "GET"],
    [RECEIVE, "POST"],
    [ACK, "POST"],
    [PAYMENT_ENTRY, "GET"],
  ]);
});

it("does not read the receipt or show confirmation when acknowledgment fails", async () => {
  setupReceiptFlow();
  const read = vi.fn(() => receipt());
  server.use(
    http.post(ACK, () => new HttpResponse(null, { status: 503 })),
    http.get(PAYMENT_ENTRY, read),
  );
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  expect(await review().findByRole("alert")).toHaveTextContent(/outcome is uncertain/i);
  expect(confirm()).toBeDisabled();
  expect(read).not.toHaveBeenCalled();
  expect(review().queryByText(/Payment ID/)).not.toBeInTheDocument();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
});

it.each(["invoice", "auth epoch"])("cancels a pending receipt and hides it after changing %s", async (change) => {
  setupReceiptFlow();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const read = vi.fn(async () => {
    await pending;
    return receipt();
  });
  server.use(http.get(PAYMENT_ENTRY, read));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  expect(review().getByText("Payment received. Payment ID: PE-TEST-1.")).toBeVisible();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
  const signal = fetchSpy.mock.calls.find(([url]) => url === PAYMENT_ENTRY)?.[1]?.signal;
  if (change === "auth epoch") {
    optIn({ sessionEpoch: "replacement-epoch" });
    await user.click(screen.getByRole("button", { name: "Refresh session" }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  }
  await user.click(await selectInvoice(anotherThb.id));
  expect(signal?.aborted).toBe(true);
  await act(async () => release());
  expect(review().getByText(anotherThb.customer)).toBeVisible();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
  expect(review().queryByText("Receipt unavailable.")).not.toBeInTheDocument();
});

it.each([
  { outstanding_amount: 2800 },
  { outstanding_amount: 2850.5001 },
  { outstanding_amount: 0 },
  { currency: "USD" },
  { status: "Paid" },
  { status: "Cancelled" },
  { docstatus: 0 },
  { name: anotherThb.id },
])("stops before posting when recheck fails or invoice changed (%j)", async (changed) => {
  optIn();
  list.mockResolvedValue({ rows: [thb], hasMore: false });
  server.use(http.get(`${SALES_INVOICE}/${thb.id}`, () => detail(changed)));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  expect(await review().findByRole("alert")).toHaveTextContent(/refresh.*review/i);
  expect(confirm()).toBeDisabled();
  expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([NATIVE_BOOTSTRAP, `${SALES_INVOICE}/${thb.id}`]);
  await user.click(review().getByRole("button", { name: "Refresh invoices" }));
  await user.click(await selectInvoice(thb.id));
  expect(list).toHaveBeenCalledTimes(2);
  expect(confirm()).toBeEnabled();
});

it("does not reuse a stale invoice snapshot after switching the selection away and back", async () => {
  optIn();
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  const read = vi.fn(() => detail({ outstanding_amount: 2800 }));
  server.use(http.get(`${SALES_INVOICE}/${thb.id}`, read));
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  await review().findByRole("alert");
  await user.click(await selectInvoice(anotherThb.id));
  expect(confirm()).toBeEnabled();
  await user.click(await selectInvoice(thb.id));
  expect(confirm()).toBeDisabled();
  expect(review().getByRole("alert")).toHaveTextContent(/refresh.*review/i);
  expect(read).toHaveBeenCalledTimes(1);
});

it("persists the uncertain stop across remount and does not dispatch a retry", async () => {
  optIn();
  list.mockResolvedValue({ rows: [thb], hasMore: false });
  const post = vi.fn(() => HttpResponse.error());
  const ack = vi.fn();
  const readReceipt = vi.fn(() => receipt());
  server.use(
    http.get(`${SALES_INVOICE}/${thb.id}`, () => detail()),
    http.post(RECEIVE, post),
    http.post(ACK, ack),
    http.get(PAYMENT_ENTRY, readReceipt),
  );
  const user = userEvent.setup();
  const first = open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  expect(await review().findByRole("alert")).toHaveTextContent("The outcome is uncertain. Do not retry");
  expect(confirm()).toBeDisabled();
  first.unmount();
  open();
  await user.click(await selectInvoice(thb.id));
  expect(review().getByRole("alert")).toHaveTextContent("The outcome is uncertain. Do not retry");
  expect(confirm()).toBeDisabled();
  await user.click(confirm());
  expect(post).toHaveBeenCalledTimes(1);
  expect(ack).not.toHaveBeenCalled();
  expect(readReceipt).not.toHaveBeenCalled();
});

it("cancels an old balance check when another invoice is selected", async () => {
  optIn();
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const read = vi.fn(async () => {
    await pending;
    return detail();
  });
  const post = vi.fn(received);
  server.use(http.get(`${SALES_INVOICE}/${thb.id}`, read), http.post(RECEIVE, post));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  const signal = fetchSpy.mock.calls.find(([url]) => url === `${SALES_INVOICE}/${thb.id}`)?.[1]?.signal;
  await user.click(await selectInvoice(anotherThb.id));
  expect(signal?.aborted).toBe(true);
  await act(async () => release());
  expect(review().getByText(anotherThb.customer)).toBeVisible();
  expect(post).not.toHaveBeenCalled();
});

it("suppresses an acknowledged result from a previous invoice", async () => {
  optIn();
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const post = vi.fn(async () => {
    await pending;
    return received();
  });
  const ack = vi.fn(() => HttpResponse.json({ message: { acknowledged: true } }));
  const readReceipt = vi.fn(() => receipt());
  server.use(
    http.get(`${SALES_INVOICE}/${thb.id}`, () => detail()),
    http.post(RECEIVE, post),
    http.post(ACK, ack),
    http.get(PAYMENT_ENTRY, readReceipt),
  );
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await user.click(await selectInvoice(anotherThb.id));
  await act(async () => release());
  await waitFor(() => {
    expect(ack).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(PAYMENT_LOCK)).toBeNull();
  });
  expect(review().getByText(anotherThb.customer)).toBeVisible();
  expect(screen.queryByText(/PE-TEST-1/)).not.toBeInTheDocument();
  expect(readReceipt).not.toHaveBeenCalled();
  expect(confirm()).toBeEnabled();
});

it("retains the uncertain lock when the auth epoch changes before a dispatched payment returns", async () => {
  optIn();
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const post = vi.fn(async () => {
    await pending;
    return received();
  });
  const ack = vi.fn(() => HttpResponse.json({ message: { acknowledged: true } }));
  const readReceipt = vi.fn(() => receipt());
  const receive = vi.spyOn(financeWrite, "receivePayment");
  server.use(
    http.get(`${SALES_INVOICE}/${thb.id}`, () => detail()),
    http.post(RECEIVE, post),
    http.post(ACK, ack),
    http.get(PAYMENT_ENTRY, readReceipt),
  );
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  await user.click(confirm());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  const outcome = receive.mock.results[0]!.value;

  optIn({ sessionEpoch: "replacement-epoch" });
  await user.click(screen.getByRole("button", { name: "Refresh session" }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(review().queryByText(thb.customer)).not.toBeInTheDocument();
  expect(review().getByText("Select an invoice to review the full amount due.")).toBeVisible();

  await act(async () => {
    release();
    await expect(outcome).rejects.toMatchObject({ code: "uncertain" });
  });
  await user.click(await selectInvoice(anotherThb.id));
  expect(review().getByText(anotherThb.customer)).toBeVisible();
  expect(review().getByRole("alert")).toHaveTextContent("The outcome is uncertain. Do not retry");
  expect(confirm()).toBeDisabled();
  await user.click(confirm());
  expect(post).toHaveBeenCalledTimes(1);
  expect(ack).not.toHaveBeenCalled();
  expect(readReceipt).not.toHaveBeenCalled();
  expect(localStorage.getItem(PAYMENT_LOCK)).toBe("1");
  expect(screen.queryByText(/PE-TEST-1/)).not.toBeInTheDocument();
  expect(review().queryByRole("link", { name: RECEIPT_NAME })).not.toBeInTheDocument();
});

it("defaults to THB, discovers eligible currencies on demand, and resets review at each currency change", async () => {
  list.mockImplementation(async ({ currency }) => ({
    rows: currency === "THB" ? [thb, anotherThb] : currency === "USD" ? [usd] : [thb, usd],
    hasMore: false,
  }));
  const user = userEvent.setup();
  open();
  const button = await selectInvoice(thb.id);
  expect(currency()).toHaveValue("THB");
  expect(list).toHaveBeenCalledWith({ currency: "THB", search: "", offset: 0 }, expect.any(AbortSignal));
  expect(within(currency()).queryByRole("option", { name: "USD" })).not.toBeInTheDocument();
  expect(await selectInvoice(anotherThb.id)).toBeVisible();
  const row = within(screen.getByRole("row", { name: new RegExp(thb.id) }));
  expect(row.getByText(thb.customerName)).toBeVisible();
  expect(row.getByText(thb.customer)).toBeVisible();
  expect(row.getByRole("cell", { name: "Patient details unavailable" })).toBeVisible();
  expect(row.getByText(thb.postingDate)).toBeVisible();
  expect(row.getByText("THB 2,850.50")).toBeVisible();
  await user.click(button);
  expect(review().getByText("Patient details unavailable")).toBeVisible();
  await user.selectOptions(currency(), "All currencies");
  expect(review().queryByText(thb.customer)).not.toBeInTheDocument();
  await selectInvoice(usd.id);
  expect(list).toHaveBeenLastCalledWith({ currency: undefined, search: "", offset: 0 }, expect.any(AbortSignal));
  await user.selectOptions(currency(), "USD");
  await user.click(await selectInvoice(usd.id));
  expect(review().getByText("Patient details unavailable")).toBeVisible();
  expect(review().getByRole("textbox", { name: "Full amount due" })).toHaveValue("USD\u00a085.50");
});

it("keeps billing identity separate from unavailable patient details and never dispatches payment", async () => {
  list.mockResolvedValue({ rows: [thb, anotherThb], hasMore: false });
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  open();
  await user.click(await selectInvoice(thb.id));
  expect(review().getByText(thb.customerName)).toBeVisible();
  expect(review().getByText(thb.customer)).toBeVisible();
  expect(review().getByText("Patient").nextElementSibling).toHaveTextContent(/^Patient details unavailable$/);
  expect(review().getByText("THB 3,500.00")).toBeVisible();
  expect(review().getByLabelText("Full amount due")).toHaveAttribute("readonly");
  expect(review().queryByLabelText("Card terminal reference")).not.toBeInTheDocument();
  const method = review().getByLabelText("Payment method");
  expect(within(method).getAllByRole("option").map((option) => option.textContent))
    .toEqual(["Cash", "Credit Card"]);
  await user.selectOptions(method, "Credit Card");
  const terminal = review().getByLabelText("Card terminal reference");
  expect(terminal).toBeRequired();
  await user.type(terminal, "TERM-492218");
  await user.keyboard("{Enter}");
  const confirm = review().getByRole("button", { name: "Confirm full payment" });
  expect(confirm).toBeDisabled();
  expect(confirm).toHaveAccessibleDescription(/full-balance settlement.*same currency/i);
  await user.click(confirm);
  await user.click(await selectInvoice(anotherThb.id));
  expect(review().getByText(anotherThb.customer)).toBeVisible();
  expect(review().getByText("Patient details unavailable")).toBeVisible();
  expect(review().queryByText(thb.customer)).not.toBeInTheDocument();
  expect(review().queryByText(/TERM-492218/)).not.toBeInTheDocument();
  expect(screen.queryByTestId("payment-write-form")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /print|receipt/i })).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
  expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"]))
    .toEqual([[NATIVE_BOOTSTRAP, "GET"]]);
});

it("pages bounded results and searches server-side using invoice ID or billing customer", async () => {
  const batch = Array.from({ length: 25 }, (_, index) => ({
    ...thb, id: `ACC-SINV-2026-00${201 + index}`,
  }));
  list.mockImplementation(async ({ search, offset }) => ({
    rows: search === anotherThb.customer ? [anotherThb] : search === thb.id ? [thb] : offset ? [anotherThb] : batch,
    hasMore: !search && !offset,
  }));
  const user = userEvent.setup();
  open();
  await selectInvoice(batch[24]!.id);
  await user.click(screen.getByRole("button", { name: "Next" }));
  await user.click(await selectInvoice(anotherThb.id));
  expect(list).toHaveBeenLastCalledWith({ currency: "THB", search: "", offset: 25 }, expect.any(AbortSignal));
  const search = screen.getByRole("searchbox", { name: "Invoice ID or billing customer ID" });
  await user.type(search, `${anotherThb.customer}{Enter}`);
  await selectInvoice(anotherThb.id);
  expect(list).toHaveBeenLastCalledWith({ currency: "THB", search: anotherThb.customer, offset: 0 }, expect.any(AbortSignal));
  expect(review().queryByText(anotherThb.customer)).not.toBeInTheDocument();
  await user.clear(search);
  await user.type(search, `${thb.id}{Enter}`);
  await selectInvoice(thb.id);
  expect(list).toHaveBeenLastCalledWith({ currency: "THB", search: thb.id, offset: 0 }, expect.any(AbortSignal));
});

it("distinguishes an empty THB list from a denied read and never substitutes demo records", async () => {
  list.mockResolvedValueOnce({ rows: [], hasMore: false })
    .mockRejectedValueOnce(new ReadError("permission"));
  open();
  expect(await screen.findByText("No unpaid submitted invoices match.")).toBeVisible();
  await userEvent.selectOptions(currency(), "All currencies");
  expect(await screen.findByRole("alert")).toHaveTextContent("You don’t have permission to view unpaid invoices.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("does not read invoices for a native user without the cashier role", async () => {
  optIn({ role: "ADM" });
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("You don’t have permission to view unpaid invoices.");
  expect(list).not.toHaveBeenCalled();
  expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"]))
    .toEqual([[NATIVE_BOOTSTRAP, "GET"]]);
});

it("aborts and ignores stale filter results and clears all invoice state on session revocation", async () => {
  let release!: (outcome: { rows: (typeof usd)[]; hasMore: boolean }) => void;
  const slow = new Promise((resolve) => { release = resolve; });
  list.mockResolvedValue({ rows: [thb], hasMore: false }).mockReturnValueOnce(slow);
  const user = userEvent.setup();
  open();
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  const old = list.mock.calls[0]![1] as AbortSignal;
  await user.selectOptions(currency(), "All currencies");
  await user.click(await selectInvoice(thb.id));
  expect(old.aborted).toBe(true);
  await act(async () => release({ rows: [usd], hasMore: false }));
  expect(screen.queryByRole("button", { name: `Select ${usd.id}` })).not.toBeInTheDocument();
  expect(within(currency()).queryByRole("option", { name: "USD" })).not.toBeInTheDocument();
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("ADM")));
  await user.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText(thb.customer)).not.toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("drops discovered currencies and an in-flight page on a new auth generation", async () => {
  let release!: (outcome: { rows: (typeof usd)[]; hasMore: boolean }) => void;
  const slow = new Promise((resolve) => { release = resolve; });
  list.mockResolvedValueOnce({ rows: [], hasMore: false })
    .mockResolvedValueOnce({ rows: [usd], hasMore: true })
    .mockReturnValueOnce(slow)
    .mockResolvedValueOnce({ rows: [thb], hasMore: false });
  const user = userEvent.setup();
  open();
  await screen.findByText("No unpaid submitted invoices match.");
  await user.selectOptions(currency(), "All currencies");
  await user.click(await selectInvoice(usd.id));
  expect(within(currency()).getByRole("option", { name: "USD" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(3));
  const pending = list.mock.calls[2]![1] as AbortSignal;
  await user.click(screen.getByRole("button", { name: "Refresh session" }));
  await selectInvoice(thb.id);
  expect(pending.aborted).toBe(true);
  await act(async () => release({ rows: [usd], hasMore: false }));
  expect(currency()).toHaveValue("THB");
  expect(within(currency()).queryByRole("option", { name: "USD" })).not.toBeInTheDocument();
  expect(review().queryByText(usd.id)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: `Select ${usd.id}` })).not.toBeInTheDocument();
});
