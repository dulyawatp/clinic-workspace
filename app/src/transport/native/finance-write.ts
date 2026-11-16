import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type FinanceWriteKind = "invoice_submit" | "payment_receive";

export type FinanceWriteFailure =
  | "locked"
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

/** One pending-write lock per kind: an invoice never blocks a payment. */
const LOCKS: Record<FinanceWriteKind, string> = {
  invoice_submit: "clinic.finance-write.pending.invoice_submit",
  payment_receive: "clinic.finance-write.pending.payment_receive",
};

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export class NativeFinanceWriteError extends Error {
  readonly code: FinanceWriteFailure;

  constructor(code: FinanceWriteFailure) {
    super(code);
    this.name = "NativeFinanceWriteError";
    this.code = code;
  }
}

export interface InvoiceLineInput {
  item_code: string;
  qty: number;
}

export interface SubmitInvoiceInput {
  operation_key: string;
  customer: string;
  selling_price_list: string;
  due_date: string;
  items: InvoiceLineInput[];
  /** An absolute discount amount; no rate is ever implied or invented. */
  discount?: number;
}

export interface PaymentAllocationInput {
  invoice: string;
  amount: number;
}

export interface ReceivePaymentInput {
  operation_key: string;
  invoice: string;
  mode_of_payment: "Cash" | "Credit Card";
  amount: number;
  allocations: PaymentAllocationInput[];
  reference_no?: string;
}

export interface InvoicePreviewLine {
  item_code: string;
  qty: number;
  rate: number;
  amount: number;
}

/** The server-resolved preview: every figure here is server truth. */
export interface InvoicePreview {
  status: "preview";
  customer: string;
  currency: string;
  company: string;
  selling_price_list: string;
  due_date: string;
  lines: InvoicePreviewLine[];
  net_total: number;
  discount: number;
  discount_available: boolean;
  total_taxes: number;
  total: number;
}

export type FinanceWriteResult =
  | { status: "submitted"; invoice: string; total: number }
  | {
      status: "received";
      payment: string;
      invoice: string;
      outstanding: number;
    };

const ENDPOINTS: Record<FinanceWriteKind, string> = {
  invoice_submit: "clinic_ui.api.finance_write.submit_invoice",
  payment_receive: "clinic_ui.api.finance_write.receive_payment",
};

const STATUSES: Record<FinanceWriteKind, string> = {
  invoice_submit: "submitted",
  payment_receive: "received",
};

const listeners = new Set<() => void>();

function notifyLock(): void {
  for (const listener of listeners) listener();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** The browser lock is the only persisted write state: no data is stored. */
export function financeWriteLocked(kind: FinanceWriteKind): boolean {
  try {
    return localStorage.getItem(LOCKS[kind]) !== null;
  } catch {
    return true;
  }
}

export function subscribeFinanceWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(kind: FinanceWriteKind): void {
  try {
    localStorage.setItem(LOCKS[kind], "1");
    if (localStorage.getItem(LOCKS[kind]) !== "1")
      throw new NativeFinanceWriteError("locked");
  } catch {
    throw new NativeFinanceWriteError("locked");
  }
}

function clearLock(kind: FinanceWriteKind): void {
  try {
    localStorage.removeItem(LOCKS[kind]);
    if (financeWriteLocked(kind))
      throw new NativeFinanceWriteError("uncertain");
  } catch {
    throw new NativeFinanceWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativeFinanceWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativeFinanceWriteError("conflict");
    return new NativeFinanceWriteError("locked");
  }
  if (status === 401) return new NativeFinanceWriteError("auth");
  if (status === 403) return new NativeFinanceWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativeFinanceWriteError("validation");
  return new NativeFinanceWriteError("uncertain");
}

function parsePreviewLine(value: unknown): InvoicePreviewLine {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["item_code", "qty", "rate", "amount"]) ||
    !isText(value.item_code) ||
    !isAmount(value.qty) ||
    !isAmount(value.rate) ||
    !isAmount(value.amount)
  )
    throw new NativeFinanceWriteError("uncertain");
  return {
    item_code: value.item_code,
    qty: value.qty,
    rate: value.rate,
    amount: value.amount,
  };
}

function parsePreview(body: unknown): InvoicePreview {
  if (!isRecord(body) || !isRecord(body.message))
    throw new NativeFinanceWriteError("uncertain");
  const message = body.message;
  if (
    !hasExactKeys(message, [
      "status",
      "customer",
      "currency",
      "company",
      "selling_price_list",
      "due_date",
      "lines",
      "net_total",
      "discount",
      "discount_available",
      "total_taxes",
      "total",
    ]) ||
    message.status !== "preview" ||
    !isText(message.customer) ||
    !isText(message.currency) ||
    !isText(message.company) ||
    !isText(message.selling_price_list) ||
    !isText(message.due_date) ||
    !Array.isArray(message.lines) ||
    !isAmount(message.net_total) ||
    !isAmount(message.discount) ||
    typeof message.discount_available !== "boolean" ||
    !isAmount(message.total_taxes) ||
    !isAmount(message.total)
  )
    throw new NativeFinanceWriteError("uncertain");
  return {
    status: "preview",
    customer: message.customer,
    currency: message.currency,
    company: message.company,
    selling_price_list: message.selling_price_list,
    due_date: message.due_date,
    lines: message.lines.map(parsePreviewLine),
    net_total: message.net_total,
    discount: message.discount,
    discount_available: message.discount_available,
    total_taxes: message.total_taxes,
    total: message.total,
  };
}

function parseWriteResult(
  kind: FinanceWriteKind,
  body: unknown,
): { result: FinanceWriteResult; ackToken: string } {
  if (!isRecord(body) || !isRecord(body.message))
    throw new NativeFinanceWriteError("uncertain");
  const message = body.message;
  if (typeof message.ackToken !== "string" || !ACK_TOKEN.test(message.ackToken))
    throw new NativeFinanceWriteError("uncertain");
  if (message.status !== STATUSES[kind])
    throw new NativeFinanceWriteError("uncertain");

  if (kind === "payment_receive") {
    if (
      !hasExactKeys(message, [
        "status",
        "payment",
        "invoice",
        "outstanding",
        "ackToken",
      ]) ||
      !isText(message.payment) ||
      !isText(message.invoice) ||
      !isAmount(message.outstanding)
    )
      throw new NativeFinanceWriteError("uncertain");
    return {
      result: {
        status: "received",
        payment: message.payment,
        invoice: message.invoice,
        outstanding: message.outstanding,
      },
      ackToken: message.ackToken,
    };
  }

  if (
    !hasExactKeys(message, ["status", "invoice", "total", "ackToken"]) ||
    !isText(message.invoice) ||
    !isAmount(message.total)
  )
    throw new NativeFinanceWriteError("uncertain");
  return {
    result: {
      status: "submitted",
      invoice: message.invoice,
      total: message.total,
    },
    ackToken: message.ackToken,
  };
}

async function acknowledge(
  token: string,
  kind: FinanceWriteKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.finance_write.ack", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      [FRAPPE_CSRF_HEADER]: token,
    },
    body: JSON.stringify({
      operation_key: operationKey,
      kind,
      ack_token: ackToken,
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (
    response.status !== 200 ||
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["acknowledged"]) ||
    body.message.acknowledged !== true
  )
    throw new NativeFinanceWriteError("uncertain");
}

/**
 * One guarded POST with one explicit acknowledgement and no retry anywhere.
 * The lock is persisted before dispatch and only cleared after a confirmed ack;
 * any uncertain outcome keeps it, so a reload shows the stop state again.
 */
async function submit(
  kind: FinanceWriteKind,
  input: Record<string, unknown>,
): Promise<FinanceWriteResult> {
  const operationKey = input.operation_key;
  if (!isText(operationKey)) throw new NativeFinanceWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || financeWriteLocked(kind))
    throw new NativeFinanceWriteError("locked");

  persistLock(kind);

  let response: Response;
  try {
    response = await frappeRequest(ENDPOINTS[kind], {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(input),
    });
  } catch {
    notifyLock();
    throw new NativeFinanceWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = failure(response.status, body);
    // A 417/422 is always a pre-fence refusal (the contract refuses before the
    // claim), so nothing was written and the kind is free again. Every other
    // outcome keeps the lock: 409 may hide an unresolved key.
    if (error.code === "validation") {
      try {
        clearLock(kind);
      } catch {
        notifyLock();
      }
    } else {
      notifyLock();
    }
    throw error;
  }

  let parsed: { result: FinanceWriteResult; ackToken: string };
  try {
    parsed = parseWriteResult(kind, body);
  } catch {
    notifyLock();
    throw new NativeFinanceWriteError("uncertain");
  }

  try {
    await acknowledge(token, kind, operationKey, parsed.ackToken);
  } catch {
    notifyLock();
    throw new NativeFinanceWriteError("uncertain");
  }

  clearLock(kind);
  return parsed.result;
}

/**
 * Fetch the server-resolved invoice preview. This writes nothing and takes no
 * lock: the client's own arithmetic is never shown as truth.
 */
export async function previewInvoice(
  input: SubmitInvoiceInput,
): Promise<InvoicePreview> {
  const token = nativeCsrfToken();
  if (!token) throw new NativeFinanceWriteError("locked");
  const response = await frappeRequest(
    "clinic_ui.api.finance_write.save_invoice_draft",
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(input),
    },
  );
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);
  return parsePreview(body);
}

export async function submitInvoice(
  input: SubmitInvoiceInput,
): Promise<FinanceWriteResult> {
  return submit("invoice_submit", {
    operation_key: input.operation_key,
    customer: input.customer,
    selling_price_list: input.selling_price_list,
    due_date: input.due_date,
    items: input.items.map((line) => ({
      item_code: line.item_code,
      qty: line.qty,
    })),
    ...(input.discount && input.discount > 0
      ? { discount: input.discount }
      : {}),
  });
}

export async function receivePayment(
  input: ReceivePaymentInput,
): Promise<FinanceWriteResult> {
  return submit("payment_receive", {
    operation_key: input.operation_key,
    invoice: input.invoice,
    mode_of_payment: input.mode_of_payment,
    amount: input.amount,
    allocations: input.allocations.map((row) => ({
      invoice: row.invoice,
      amount: row.amount,
    })),
    ...(input.reference_no?.trim()
      ? { reference_no: input.reference_no.trim() }
      : {}),
  });
}
