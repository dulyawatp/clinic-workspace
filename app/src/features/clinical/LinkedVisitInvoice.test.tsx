import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import {
  getHealthcareInvoiceContext,
  prepareHealthcareInvoice,
  readHealthcareInvoice,
} from "../../transport/native/native-healthcare-invoice";
import { NativeInvoiceHttpError, type NativeInvoiceResult } from "../../transport/native/native-invoice";
import { createNativeInvoiceActions } from "../../transport/native/native-invoice-actions";
import type { DoctorInvoiceSubmitResult } from "./DoctorInvoicePanel";
import { LinkedVisitInvoice } from "./LinkedVisitInvoice";

vi.mock("../../transport/native/native-healthcare-invoice", () => ({
  getHealthcareInvoiceContext: vi.fn(),
  prepareHealthcareInvoice: vi.fn(),
  readHealthcareInvoice: vi.fn(),
}));
vi.mock("../../transport/native/native-invoice-actions", () => ({ createNativeInvoiceActions: vi.fn() }));

const sources = [
  { service: "Laser", reference_type: "Clinical Procedure", reference_name: "PROC-100", qty: 1 },
  { service: "Consultation", reference_type: "Patient Encounter", reference_name: "ENC-200", qty: 2 },
];
const nativeContext = {
  appointment: "APPT-100", patient: "PAT-100", company: "Clinic", customer: "Customer", rows: sources,
};
const doc: NativeInvoiceResult = {
  doctype: "Sales Invoice", docstatus: 0, currency: "THB", items: [{ item_code: "Consultation", qty: 2 }],
};
const preparation = { currency: "THB", lines: [{ id: "consultation", description: "Consultation", quantity: 2 }] };
const reviewInvoice = vi.fn(async () => ({
  ...preparation, taxes: [], subtotal: 500, discount: 0, taxTotal: 0, grandTotal: 500, roundedTotal: 500,
}));
const submitInvoice = vi.fn(async (): Promise<DoctorInvoiceSubmitResult> => ({ status: "unknown" }));
const checkInvoiceStatus = vi.fn(async () => ({ status: "unknown" as const }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getHealthcareInvoiceContext).mockResolvedValue(nativeContext);
  vi.mocked(prepareHealthcareInvoice).mockResolvedValue(doc);
  vi.mocked(createNativeInvoiceActions).mockReturnValue({ preparation, reviewInvoice, submitInvoice, checkInvoiceStatus });
});

function renderInvoice(appointment = "APPT-100", locale: "en" | "th" = "en") {
  const tree = (name: string) => <LocaleProvider initial={locale}><MemoryRouter>
    <LinkedVisitInvoice appointment={name} />
  </MemoryRouter></LocaleProvider>;
  const view = render(tree(appointment));
  return { update: (name: string) => view.rerender(tree(name)) };
}

const button = (name: string) => screen.getByRole("button", { name });

it("loads only on open with the native appointment and prepares only explicitly selected native rows", async () => {
  const user = userEvent.setup();
  renderInvoice();
  expect(getHealthcareInvoiceContext).not.toHaveBeenCalled();
  await user.click(button("Visit invoice"));
  expect(getHealthcareInvoiceContext).toHaveBeenCalledExactlyOnceWith("APPT-100", expect.any(AbortSignal));
  expect(screen.getByRole("heading", { name: "Patient billable services" })).toHaveFocus();
  expect(screen.getByText(/not just this visit/i)).toBeInTheDocument();
  expect(screen.getByText("Clinical Procedure · PROC-100")).toBeInTheDocument();
  expect(screen.getByText("Patient Encounter · ENC-200")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Laser" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Consultation" })).not.toBeChecked();
  expect(button("Prepare invoice")).toBeDisabled();
  expect(prepareHealthcareInvoice).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox", { name: "Consultation" }));
  await user.click(button("Prepare invoice"));
  expect(prepareHealthcareInvoice).toHaveBeenCalledExactlyOnceWith(nativeContext, [sources[1]], expect.any(AbortSignal));
  expect(vi.mocked(prepareHealthcareInvoice).mock.calls[0]?.[1][0]).toBe(sources[1]);
  expect(createNativeInvoiceActions).toHaveBeenCalledExactlyOnceWith(doc, readHealthcareInvoice);
  expect(button("Review total")).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Submit Invoice" })).not.toBeInTheDocument();
});

it("allows correction before submit but locks source changes when a write may have landed", async () => {
  const user = userEvent.setup();
  renderInvoice();
  await user.click(button("Visit invoice"));
  await user.click(screen.getByRole("checkbox", { name: "Laser" }));
  await user.click(button("Prepare invoice"));
  await user.click(button("Change selected services"));
  expect(screen.getByRole("checkbox", { name: "Laser" })).toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: "Laser" }));
  await user.click(screen.getByRole("checkbox", { name: "Consultation" }));
  await user.click(button("Prepare invoice"));
  await user.click(button("Review total"));
  let resolve!: (result: DoctorInvoiceSubmitResult) => void;
  submitInvoice.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  await user.click(button("Submit Invoice"));
  expect(screen.queryByRole("button", { name: "Change selected services" })).not.toBeInTheDocument();
  await act(async () => resolve({ status: "unknown" }));
  expect(checkInvoiceStatus).not.toHaveBeenCalled();
  await user.click(button("Check invoice status"));
  expect(createNativeInvoiceActions).toHaveBeenCalledTimes(2);
  expect(prepareHealthcareInvoice).toHaveBeenCalledTimes(2);
  expect(submitInvoice).toHaveBeenCalledTimes(1);
  expect(checkInvoiceStatus).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it.each([
  { setup: () => vi.mocked(getHealthcareInvoiceContext).mockRejectedValueOnce(new NativeInvoiceHttpError(403, "Private backend details")), text: "Marley did not allow access to these billable services." },
  { setup: () => vi.mocked(getHealthcareInvoiceContext).mockResolvedValueOnce({ ...nativeContext, rows: [] }), text: "No billable services were returned for this patient." },
  { setup: () => vi.mocked(getHealthcareInvoiceContext).mockRejectedValueOnce(new Error("Private backend details")), text: "Billable services are unavailable right now." },
])("shows $text without backend details or an active prepare action", async ({ setup, text }) => {
  const user = userEvent.setup();
  setup();
  renderInvoice();
  await user.click(button("Visit invoice"));
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(screen.queryByText(/Private backend details/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Prepare invoice" })).not.toBeInTheDocument();
  expect(prepareHealthcareInvoice).not.toHaveBeenCalled();
});

it("shows an unmapped native source and preserves explicit selection on preparation denial", async () => {
  const user = userEvent.setup();
  const unmapped = { service: null, reference_type: "Lab Test", reference_name: "LAB-300", qty: "3" };
  vi.mocked(getHealthcareInvoiceContext).mockResolvedValueOnce({ ...nativeContext, rows: [...sources, unmapped] });
  vi.mocked(prepareHealthcareInvoice).mockRejectedValueOnce(new NativeInvoiceHttpError(403, "Private backend details"));
  renderInvoice();
  await user.click(button("Visit invoice"));
  expect(screen.getByText("Lab Test · LAB-300")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "No invoice item linked" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "Consultation" }));
  await user.click(button("Prepare invoice"));
  expect(screen.getByText("Marley did not allow this invoice to be prepared.")).toHaveFocus();
  expect(screen.getByRole("checkbox", { name: "Consultation" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Laser" })).not.toBeChecked();
  expect(screen.queryByText("Private backend details")).not.toBeInTheDocument();
  expect(createNativeInvoiceActions).not.toHaveBeenCalled();
});

it("does not surface an old lookup after appointment change and supports Thai launch", async () => {
  const user = userEvent.setup();
  let complete!: (result: typeof nativeContext) => void;
  vi.mocked(getHealthcareInvoiceContext).mockReturnValueOnce(new Promise((resolve) => { complete = resolve; }));
  const { update } = renderInvoice("APPT-100", "th");
  await user.click(button("ใบแจ้งหนี้สำหรับการเข้ารับบริการ"));
  update("APPT-200");
  const firstSignal = vi.mocked(getHealthcareInvoiceContext).mock.calls[0]?.[1];
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => complete(nativeContext));
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  await user.click(button("ใบแจ้งหนี้สำหรับการเข้ารับบริการ"));
  expect(getHealthcareInvoiceContext).toHaveBeenLastCalledWith("APPT-200", expect.any(AbortSignal));
});
