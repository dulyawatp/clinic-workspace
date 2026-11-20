import { z } from "zod";

const text = z.string().max(1024).refine((value) =>
  Boolean(value.trim()) && value !== "XXXXXXXX" && !/[\u0000-\u001f\u007f]/.test(value));
const id = text.refine((value) =>
  value.length <= 256 && value === value.trim() && value !== "." && value !== ".." && !/[/\\%]/.test(value));
const amount = z.number().finite();
const optionalText = text.nullish();
const optionalAmount = amount.nullish();
const invoice = z.object({
  name: id, docstatus: z.literal(1), status: text, customer: text, company: text,
  posting_date: text, due_date: optionalText, currency: text,
  total: amount, discount_amount: amount, net_total: amount, total_taxes_and_charges: amount,
  grand_total: amount, rounded_total: optionalAmount, outstanding_amount: amount,
  items: z.array(z.object({
    item_code: optionalText, item_name: text, qty: amount, uom: text,
    rate: amount, amount, net_amount: amount,
  })).min(1).max(1000),
  taxes: z.array(z.object({
    description: text, rate: optionalAmount, tax_amount: z.unknown().optional(),
    tax_amount_after_discount_amount: z.unknown().optional(),
  }).transform((row, ctx) => {
    const value = amount.safeParse(row.tax_amount_after_discount_amount);
    const fallback = amount.safeParse(row.tax_amount);
    if (!value.success && !fallback.success) {
      ctx.addIssue({ code: "custom", message: "Missing tax amount" });
      return z.NEVER;
    }
    return { ...row, actualAmount: value.success ? value.data : fallback.data! };
  })).max(1000),
});

export type NativePostedInvoiceDetail = {
  id: string; status: string; customer: string; company: string; postingDate: string; dueDate?: string;
  currency: string; subtotal: number; discount: number; netTotal: number; taxTotal: number;
  grandTotal: number; roundedTotal?: number; outstanding: number;
  items: { code?: string; name: string; quantity: number; uom: string; rate: number; amount: number; netAmount: number }[];
  taxes: { description: string; rate?: number; amount: number }[];
};

export class NativePostedInvoiceError extends Error {
  constructor(public reason: "denied" | "unavailable" | "unsupported") {
    super(reason);
  }
}

export async function getNativePostedInvoiceDetail(invoiceId: string, signal?: AbortSignal): Promise<NativePostedInvoiceDetail> {
  if (!id.safeParse(invoiceId).success) throw new NativePostedInvoiceError("unsupported");
  let response: Response;
  try {
    response = await fetch(`/api/resource/Sales%20Invoice/${encodeURIComponent(invoiceId)}`, {
      credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
  } catch {
    throw new NativePostedInvoiceError("unavailable");
  }
  if (response.status === 401 || response.status === 403) throw new NativePostedInvoiceError("denied");
  if (!response.ok) throw new NativePostedInvoiceError("unavailable");
  const body: unknown = await response.json().catch(() => null);
  const parsed = z.object({ data: invoice }).safeParse(body);
  if (!parsed.success || parsed.data.data.name !== invoiceId) throw new NativePostedInvoiceError("unavailable");
  const doc = parsed.data.data;
  return {
    id: doc.name, status: doc.status, customer: doc.customer, company: doc.company,
    postingDate: doc.posting_date, ...(doc.due_date != null ? { dueDate: doc.due_date } : {}),
    currency: doc.currency, subtotal: doc.total, discount: doc.discount_amount, netTotal: doc.net_total,
    taxTotal: doc.total_taxes_and_charges, grandTotal: doc.grand_total,
    ...(doc.rounded_total != null ? { roundedTotal: doc.rounded_total } : {}), outstanding: doc.outstanding_amount,
    items: doc.items.map((item) => ({
      ...(item.item_code != null ? { code: item.item_code } : {}), name: item.item_name, quantity: item.qty,
      uom: item.uom, rate: item.rate, amount: item.amount, netAmount: item.net_amount,
    })),
    taxes: doc.taxes.map((tax) => ({
      description: tax.description, ...(tax.rate != null ? { rate: tax.rate } : {}),
      amount: tax.actualAmount,
    })),
  };
}
