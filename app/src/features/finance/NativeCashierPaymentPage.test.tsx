import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";
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
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("ADM")));
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
