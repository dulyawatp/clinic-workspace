import type { DoctorInvoicePanelProps, DoctorInvoicePreparation, DoctorInvoiceReview, DoctorInvoiceReviewInput } from "../../features/clinical/DoctorInvoicePanel";
import { insertNativeInvoice, NativeInvoiceHttpError, previewNativeInvoice, submitNativeInvoice, type NativeInvoiceDoc } from "./native-invoice";

type RecordValue = Record<string, unknown>;
type Actions = Omit<DoctorInvoicePanelProps, "sourceKey">;

function record(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Invalid native invoice amount");
  return value;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function preparation(doc: RecordValue): DoctorInvoicePreparation {
  if (!Array.isArray(doc.items) || !doc.items.every(record)) throw new Error("Invalid native invoice items");
  const basis = doc.apply_discount_on;
  return {
    currency: text(doc.currency),
    lines: doc.items.map((item, index) => ({
      id: `${text(item.name, text(item.item_code, "row"))}-${index}`,
      description: text(item.item_name, text(item.description, text(item.item_code))),
      quantity: number(item.qty),
      ...(item.amount == null ? {} : { amount: number(item.amount) }),
    })),
    ...(typeof doc.taxes_and_charges === "string" ? { taxTemplate: doc.taxes_and_charges } : {}),
    ...(basis === "Grand Total" || basis === "Net Total" ? { applyDiscountOn: basis } : {}),
  };
}

function review(doc: RecordValue): DoctorInvoiceReview {
  if (doc.taxes != null && (!Array.isArray(doc.taxes) || !doc.taxes.every(record))) throw new Error("Invalid native invoice taxes");
  const taxes: RecordValue[] = Array.isArray(doc.taxes) ? doc.taxes : [];
  return {
    ...preparation(doc),
    taxes: taxes.map((tax) => ({ label: text(tax.description, text(tax.account_head)), amount: number(tax.tax_amount) })),
    subtotal: number(doc.total),
    discount: number(doc.discount_amount),
    taxTotal: number(doc.total_taxes_and_charges),
    grandTotal: number(doc.grand_total),
    roundedTotal: number(doc.rounded_total),
  };
}

function withDiscount(doc: NativeInvoiceDoc, input: DoctorInvoiceReviewInput): NativeInvoiceDoc {
  const value = input.discount.kind === "none" ? 0 : Number(input.discount.value);
  if (!Number.isFinite(value)) throw new Error("Invalid discount");
  return {
    ...doc,
    discount_amount: input.discount.kind === "fixed" ? value : 0,
    additional_discount_percentage: input.discount.kind === "percent" ? value : 0,
  };
}

function definitive(error: unknown): boolean {
  return error instanceof NativeInvoiceHttpError && error.status >= 400 && error.status < 500 &&
    record(error.body) && (typeof error.body.exception === "string" || typeof error.body.exc === "string");
}

function unsavedDocument(doc: RecordValue): RecordValue {
  const metadata = new Set(["name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parenttype", "parentfield", "flags"]);
  return Object.fromEntries(Object.entries(doc)
    .filter(([key]) => !metadata.has(key) && !key.startsWith("__"))
    .map(([key, value]) => [key, Array.isArray(value)
      ? value.map((row) => record(row) ? unsavedDocument(row) : row)
      : value]));
}

function issued(doc: RecordValue, id: string) {
  return { status: "issued" as const, invoice: {
    id, currency: text(doc.currency), grandTotal: number(doc.grand_total), roundedTotal: number(doc.rounded_total),
  } };
}

/** Bind one prepared draft to panel actions. Readback must fetch exactly the requested invoice name. */
export function createNativeInvoiceActions(
  draft: NativeInvoiceDoc,
  readDocument: (name: string) => Promise<unknown>,
): Actions {
  const cleanDraft = unsavedDocument(draft) as NativeInvoiceDoc;
  const initial = preparation(cleanDraft);
  let lastReview: { input: string; total: number; rounded: number } | undefined;
  return {
    preparation: initial,
    async reviewInvoice(input) {
      const result = review(await previewNativeInvoice(withDiscount(cleanDraft, input)));
      lastReview = { input: JSON.stringify(input), total: result.grandTotal, rounded: result.roundedTotal };
      return result;
    },
    async submitInvoice(input) {
      let saved;
      try {
        saved = await insertNativeInvoice(withDiscount(cleanDraft, input));
      } catch (error) {
        return definitive(error) ? { status: "rejected" } : { status: "unknown" };
      }
      const id = saved.name!;
      if (lastReview?.input === JSON.stringify(input)) {
        try {
          const actual = review(saved);
          if (actual.grandTotal !== lastReview.total || actual.roundedTotal !== lastReview.rounded)
            return { status: "draft", draft: { id, document: saved, review: actual } };
        } catch {
          return { status: "draft", draft: { id, document: saved } };
        }
      }
      try {
        return issued(await submitNativeInvoice(saved), id);
      } catch (error) {
        return { status: definitive(error) ? "draft" : "unknown", draft: { id } };
      }
    },
    async checkInvoiceStatus({ draftId }) {
      if (!draftId) return { status: "unknown" };
      const known = { id: draftId };
      try {
        const doc = await readDocument(draftId);
        if (!record(doc) || doc.doctype !== "Sales Invoice" || doc.name !== draftId)
          return { status: "unknown", draft: known };
        if (doc.docstatus === 1) return issued(doc, draftId);
      } catch {
        // A failed read or not-found response cannot prove noncreation.
      }
      return { status: "unknown", draft: known };
    },
  };
}
