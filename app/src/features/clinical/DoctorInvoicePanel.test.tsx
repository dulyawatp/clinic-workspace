import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import {
  DoctorInvoicePanel,
  type DoctorInvoicePanelProps,
  type DoctorInvoiceReview,
} from "./DoctorInvoicePanel";

const preparation = {
  currency: "THB",
  lines: [{ id: "line-1", description: "Laser treatment", quantity: 1 }],
  taxTemplate: "Thailand Sales Tax",
  applyDiscountOn: "Net Total",
} as const;
const review: DoctorInvoiceReview = {
  ...preparation,
  lines: [{ ...preparation.lines[0], amount: 200 }],
  taxes: [{ label: "VAT 7%", amount: 11.9 }],
  subtotal: 200, discount: 30, taxTotal: 11.9, grandTotal: 181.9, roundedTotal: 182,
};
const invoice = { id: "SINV-100", currency: "THB", grandTotal: 181.9, roundedTotal: 182 };
const draft = { id: "SINV-100", viewHref: "/doctor/invoices/SINV-100" };
const noDiscount = { discount: { kind: "none" } };

function setup(props: Partial<DoctorInvoicePanelProps> = {}, locale: "en" | "th" = "en") {
  const reviewInvoice = vi.fn<DoctorInvoicePanelProps["reviewInvoice"]>().mockResolvedValue(review);
  const submitInvoice = vi.fn<DoctorInvoicePanelProps["submitInvoice"]>().mockResolvedValue({ status: "issued", invoice });
  const checkInvoiceStatus = vi.fn<DoctorInvoicePanelProps["checkInvoiceStatus"]>().mockResolvedValue({ status: "unknown" });
  const panel = (overrides?: Partial<DoctorInvoicePanelProps>) => <LocaleProvider initial={locale}>
    <MemoryRouter>
      <DoctorInvoicePanel sourceKey="draft-source-1" {...{ preparation, reviewInvoice, submitInvoice, checkInvoiceStatus }} {...props} {...overrides} />
    </MemoryRouter>
  </LocaleProvider>;
  const { rerender } = render(panel());
  return { reviewInvoice, submitInvoice, checkInvoiceStatus, update: (overrides: Partial<DoctorInvoicePanelProps>) => rerender(panel(overrides)) };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const button = (name: string) => screen.getByRole("button", { name });
const submit = () => screen.queryByRole("button", { name: "Submit Invoice" });
const discount = () => screen.getByRole("combobox", { name: "Additional Discount" });

it("shows native prepared lines, tax template and actual server-reviewed taxes and totals", async () => {
  const user = userEvent.setup();
  const { reviewInvoice, submitInvoice } = setup();
  expect(screen.getByText("Laser treatment")).toBeInTheDocument();
  expect(screen.getByText("Thailand Sales Tax")).toBeInTheDocument();
  expect(screen.getByText("Apply Additional Discount On: Net Total")).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: /tax/i })).not.toBeInTheDocument();
  expect(screen.queryByText("Rounded Total")).not.toBeInTheDocument();
  expect(submit()).not.toBeInTheDocument();

  await user.selectOptions(discount(), "fixed");
  await user.type(screen.getByRole("spinbutton", { name: "Discount Amount" }), "30");
  await user.click(button("Review total"));
  const input = { discount: { kind: "fixed", value: "30" } };
  expect(reviewInvoice).toHaveBeenLastCalledWith(input);
  const totals = screen.getByRole("region", { name: "Reviewed total" });
  expect(totals.querySelector("h3")).toHaveFocus();
  await user.tab();
  expect(submit()).toHaveFocus();
  for (const [label, amount] of [["Subtotal", "200.00"], ["Additional Discount", "30.00"], ["Taxes", "11.90"], ["Grand Total", "181.90"], ["Rounded Total", "182.00"], ["VAT 7%", "11.90"]] as const) {
    expect(within(totals).getByText(label).nextElementSibling).toHaveTextContent(amount);
  }
  await user.keyboard("{Enter}");
  expect(submitInvoice).toHaveBeenCalledExactlyOnceWith(input);
  expect(screen.getByRole("heading", { name: "Invoice issued" })).toHaveFocus();
  expect(screen.queryByRole("link", { name: "View invoice" })).not.toBeInTheDocument();
});

it("validates only numeric shape, leaves ranges to Frappe, and clears review on edit", async () => {
  const user = userEvent.setup();
  const { reviewInvoice, submitInvoice } = setup();
  await user.selectOptions(discount(), "percent");
  expect(button("Review total")).toBeDisabled();
  const percent = screen.getByRole("spinbutton", { name: "Discount Percentage" });
  await user.type(percent, "120");
  await user.click(button("Review total"));
  expect(reviewInvoice).toHaveBeenLastCalledWith({ discount: { kind: "percent", value: "120" } });
  expect(submit()).toBeEnabled();
  await user.clear(percent);
  await user.type(percent, "-5");
  expect(submit()).not.toBeInTheDocument();
  await user.click(button("Review total"));
  expect(reviewInvoice).toHaveBeenLastCalledWith({ discount: { kind: "percent", value: "-5" } });
  await user.click(button("Submit Invoice"));
  expect(submitInvoice).toHaveBeenCalledExactlyOnceWith({ discount: { kind: "percent", value: "-5" } });
});

it("discards stale in-flight review on edit or when preparation changes", async () => {
  const user = userEvent.setup();
  const { reviewInvoice, update } = setup();
  const first = deferred<DoctorInvoiceReview>();
  const second = deferred<DoctorInvoiceReview>();
  reviewInvoice.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  await user.click(button("Review total"));
  expect(reviewInvoice).toHaveBeenLastCalledWith(noDiscount);
  await user.selectOptions(discount(), "fixed");
  await user.type(screen.getByRole("spinbutton", { name: "Discount Amount" }), "20");
  await user.click(button("Review total"));
  await act(async () => first.resolve(review));
  expect(submit()).not.toBeInTheDocument();
  update({ preparation: { ...preparation, taxTemplate: "Updated default template" } });
  await act(async () => second.resolve(review));
  expect(submit()).not.toBeInTheDocument();
  expect(button("Review total")).toBeEnabled();
});

it("shows a persisted native draft after insert succeeds but submit fails, without offering another insert", async () => {
  const user = userEvent.setup();
  const { submitInvoice, checkInvoiceStatus } = setup();
  submitInvoice.mockResolvedValueOnce({ status: "draft", draft });
  await user.click(button("Review total"));
  await user.click(button("Submit Invoice"));
  expect(screen.getByRole("heading", { name: "Draft invoice saved" })).toHaveFocus();
  expect(screen.getByText(draft.id)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View invoice" })).toHaveAttribute("href", draft.viewHref);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(submitInvoice).toHaveBeenCalledExactlyOnceWith(noDiscount);
  expect(checkInvoiceStatus).not.toHaveBeenCalled();
});

it("dispatches once and uses known native draft identity to check an uncertain submit without unlocking creation", async () => {
  const user = userEvent.setup();
  const pending = deferred<Awaited<ReturnType<DoctorInvoicePanelProps["submitInvoice"]>>>();
  const { submitInvoice, checkInvoiceStatus } = setup();
  submitInvoice.mockReturnValueOnce(pending.promise);
  await user.click(button("Review total"));
  const issue = button("Submit Invoice");
  fireEvent.click(issue);
  fireEvent.click(issue);
  expect(submitInvoice).toHaveBeenCalledExactlyOnceWith(noDiscount);
  await act(async () => pending.resolve({ status: "unknown", draft }));
  expect(screen.getByText(draft.id)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review total" })).not.toBeInTheDocument();
  expect(checkInvoiceStatus).not.toHaveBeenCalled();
  checkInvoiceStatus.mockResolvedValueOnce({ status: "confirmed_absent" });
  await user.click(button("Check invoice status"));
  expect(checkInvoiceStatus).toHaveBeenLastCalledWith({ draftId: draft.id });
  expect(button("Check invoice status")).toBeEnabled();
  checkInvoiceStatus.mockResolvedValueOnce({ status: "issued", invoice: { ...invoice, viewHref: draft.viewHref } });
  await user.click(button("Check invoice status"));
  expect(screen.getByRole("heading", { name: "Invoice issued" })).toHaveFocus();
  expect(screen.getByRole("link", { name: "View invoice" })).toBeInTheDocument();
  expect(submitInvoice).toHaveBeenCalledTimes(1);
});

it("requires independent confirmed absence and fresh review after a lost insert response", async () => {
  const user = userEvent.setup();
  const { submitInvoice, reviewInvoice, checkInvoiceStatus } = setup();
  submitInvoice.mockRejectedValueOnce(new Error("Network lost"));
  await user.click(button("Review total"));
  await user.click(button("Submit Invoice"));
  expect(checkInvoiceStatus).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Review total" })).not.toBeInTheDocument();
  await user.click(button("Check invoice status"));
  expect(checkInvoiceStatus).toHaveBeenLastCalledWith({});
  expect(button("Check invoice status")).toBeEnabled();
  checkInvoiceStatus.mockResolvedValueOnce({ status: "confirmed_absent" });
  await user.click(button("Check invoice status"));
  expect(submit()).not.toBeInTheDocument();
  expect(reviewInvoice).toHaveBeenCalledTimes(1);
  expect(submitInvoice).toHaveBeenCalledTimes(1);
  await user.click(button("Review total"));
  expect(submit()).toBeEnabled();
});

it("ends recovery on an independently discovered draft and never opens a second creation path", async () => {
  const user = userEvent.setup();
  const { submitInvoice, checkInvoiceStatus } = setup();
  submitInvoice.mockResolvedValueOnce({ status: "unknown" });
  checkInvoiceStatus.mockResolvedValueOnce({ status: "draft", draft: { id: draft.id } });
  await user.click(button("Review total"));
  await user.click(button("Submit Invoice"));
  await user.click(button("Check invoice status"));
  expect(screen.getByRole("heading", { name: "Draft invoice saved" })).toHaveFocus();
  expect(screen.getByText(draft.id)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "View invoice" })).not.toBeInTheDocument();
  expect(submitInvoice).toHaveBeenCalledTimes(1);
});

it("handles native rejection without inventing a visit authority gate", async () => {
  const user = userEvent.setup();
  const { reviewInvoice, submitInvoice } = setup();
  reviewInvoice.mockRejectedValueOnce(new Error("Native calculation refused"));
  await user.click(button("Review total"));
  expect(screen.getByText("Frappe could not review the total.")).toHaveFocus();
  await user.click(button("Review total"));
  submitInvoice.mockResolvedValueOnce({ status: "rejected" });
  await user.click(button("Submit Invoice"));
  expect(screen.getByText("Frappe did not create an invoice.")).toHaveFocus();
  expect(submit()).not.toBeInTheDocument();
  expect(button("Review total")).toBeEnabled();
});

it("shows Thai labels on the native review and submission path", async () => {
  const user = userEvent.setup();
  setup({}, "th");
  expect(screen.getByRole("heading", { name: "ใบแจ้งหนี้การขาย" })).toBeInTheDocument();
  expect(screen.getByText("แม่แบบภาษี")).toBeInTheDocument();
  await user.click(button("ตรวจสอบยอดรวม"));
  expect(screen.getByRole("heading", { name: "ยอดรวมที่ตรวจสอบแล้ว" })).toHaveFocus();
  expect(screen.getByText("ยอดรวมหลังปัดเศษ")).toBeInTheDocument();
  expect(button("ยืนยันใบแจ้งหนี้")).toBeEnabled();
});
