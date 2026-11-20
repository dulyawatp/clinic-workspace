import { z } from "zod";

type Settlement = {
  payment: string;
  invoice: string;
  amount: number;
  currency: string;
  mode: "Cash" | "Credit Card";
};

/** Return a native print link only for the submitted entry that matches the settlement. */
export async function getNativePaymentReceipt(
  { payment, invoice, amount, currency, mode }: Settlement,
  signal: AbortSignal,
): Promise<string | null> {
  if (!payment || payment.length > 256 || payment !== payment.trim() ||
    payment === "." || payment === ".." || /[/\\%\u0000-\u001f\u007f]/.test(payment)) return null;

  const response = await fetch(`/api/resource/Payment%20Entry/${encodeURIComponent(payment)}`, {
    credentials: "same-origin", cache: "no-store", redirect: "error", signal,
  });
  if (response.status !== 200) return null;
  const receipt = z.object({
    data: z.object({
      name: z.literal(payment),
      docstatus: z.literal(1),
      payment_type: z.literal("Receive"),
      mode_of_payment: z.literal(mode),
      paid_from_account_currency: z.literal(currency),
      paid_to_account_currency: z.literal(currency),
      paid_amount: z.literal(amount),
      received_amount: z.literal(amount),
      references: z.tuple([z.object({
        reference_doctype: z.literal("Sales Invoice"),
        reference_name: z.literal(invoice),
        allocated_amount: z.literal(amount),
      })]),
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!receipt.safeParse(body).success) return null;

  // The native print view independently checks print permission when opened.
  const params = new URLSearchParams({
    doctype: "Payment Entry", name: payment, format: "Standard", no_letterhead: "1",
  });
  return `/printview?${params}`;
}
