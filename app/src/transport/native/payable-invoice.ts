import { z } from "zod";

const text = z.string().max(1024).refine((value) =>
  Boolean(value.trim()) && value !== "XXXXXXXX" && !/[\u0000-\u001f\u007f]/.test(value));
const currencyCode = z.string().regex(/^[A-Z]{3}$/);
const optionalText = (value: unknown): string | undefined =>
  text.safeParse(value).success ? value as string : undefined;
const isAbortError = (error: unknown): boolean =>
  typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
const invoice = z.object({
  name: text, customer: text, customer_name: z.unknown().optional(),
  posting_date: text, currency: currencyCode, grand_total: z.number().finite(),
  outstanding_amount: z.number().finite().positive(), docstatus: z.literal(1),
});

export type NativePayableInvoice = {
  id: string; customer: string; customerName?: string; postingDate: string;
  currency: string; total: number; outstanding: number;
};

export const PAYABLE_INVOICE_PAGE_SIZE = 25;

export class PayableInvoiceReadError extends Error {
  constructor(public kind: "permission" | "unavailable") {
    super(kind);
  }
}

export async function listNativePayableInvoices(
  { currency, search, offset = 0 }: { currency?: string; search?: string; offset?: number },
  signal?: AbortSignal,
): Promise<{ rows: NativePayableInvoice[]; hasMore: boolean }> {
  if (!Number.isSafeInteger(offset) || offset < 0 ||
      (currency !== undefined && !currencyCode.safeParse(currency).success) ||
      (search !== undefined && (typeof search !== "string" || search.length > 1024))) {
    throw new PayableInvoiceReadError("unavailable");
  }
  const filters: (string | number)[][] = [["docstatus", "=", 1], ["outstanding_amount", ">", 0]];
  if (currency !== undefined) filters.push(["currency", "=", currency]);
  const params = new URLSearchParams({
    fields: JSON.stringify(["name", "customer", "customer_name", "posting_date", "currency", "grand_total", "outstanding_amount", "docstatus"]),
    filters: JSON.stringify(filters), order_by: "posting_date desc, name desc",
    limit_start: String(offset), limit_page_length: String(PAYABLE_INVOICE_PAGE_SIZE + 1),
  });
  if (search?.trim()) {
    const value = `%${search.trim()}%`;
    params.set("or_filters", JSON.stringify([["name", "like", value], ["customer", "like", value]]));
  }
  let response: Response;
  try {
    response = await fetch(`/api/resource/Sales%20Invoice?${params}`, {
      credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
  } catch (error) {
    if (signal?.aborted || isAbortError(error)) throw error;
    throw new PayableInvoiceReadError("unavailable");
  }
  if (response.status === 401 || response.status === 403) throw new PayableInvoiceReadError("permission");
  if (!response.ok) throw new PayableInvoiceReadError("unavailable");
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (signal?.aborted || isAbortError(error)) throw error;
    throw new PayableInvoiceReadError("unavailable");
  }
  const parsed = z.object({ data: z.array(invoice).max(PAYABLE_INVOICE_PAGE_SIZE + 1) }).safeParse(body);
  if (!parsed.success || (currency !== undefined && parsed.data.data.some((row) => row.currency !== currency))) {
    throw new PayableInvoiceReadError("unavailable");
  }
  return {
    hasMore: parsed.data.data.length > PAYABLE_INVOICE_PAGE_SIZE,
    rows: parsed.data.data.slice(0, PAYABLE_INVOICE_PAGE_SIZE).map((row) => {
      const customerName = optionalText(row.customer_name);
      return {
        id: row.name, customer: row.customer, ...(customerName ? { customerName } : {}),
        postingDate: row.posting_date, currency: row.currency, total: row.grand_total,
        outstanding: row.outstanding_amount,
      };
    }),
  };
}
