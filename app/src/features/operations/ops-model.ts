/**
 * Read models for the operations surfaces.
 *
 * Every row comes from the Demo/Sample fixture layer (`fixtures/data-ops`,
 * `fixtures/data-authored`, `fixtures/data`); nothing here performs a read or
 * a write. Values the prototype computes (balance usability, reorder
 * thresholds, PO progress, due balances, shift totals) are read from the task-2
 * helpers instead of being recomputed a second time, so the screens and the
 * fixtures cannot disagree.
 */
import {
  type CashierShiftTotals,
  type RefundRequest,
  cashierShiftTotals,
  checkoutSamples,
  reconciliationRequests,
  refundRequests,
  sampleCancelledInvoice,
  sampleDraftInvoice,
  sampleTransfers,
  sampleVoidedPayment,
} from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import {
  COMPANY_ID,
  type ExpiryRow,
  type Invoice,
  type InvoiceStatus,
  type PoProgress,
  type PurchaseOrder,
  opsData,
} from "../../fixtures/data-ops";

export const ALL_SCOPE = "all";

// --------------------------------------------------------------------- labels
export function warehouseName(id: string, locale: "th" | "en"): string {
  return opsData.warehouses.find((row) => row.id === id)?.name[locale] ?? id;
}

export function itemName(id: string, locale: "th" | "en"): string {
  return opsData.itemById(id)?.name[locale] ?? id;
}

export function supplierName(id: string, locale: "th" | "en"): string {
  return opsData.supplierById(id).name[locale];
}

export function patientLabel(patientId: string, locale: "th" | "en"): string {
  return coreData.patientName(patientId, locale);
}

/**
 * Display mapping for the sample company value. The fixture stores the
 * Frappe-style `Demo Clinic (DC)`; the design shows the clinic name without the
 * accounting abbreviation.
 */
export function companyDisplayKey(companyId: string | null): string | null {
  if (companyId === null || companyId === "") return null;
  return companyId === COMPANY_ID ? "ops.vocab.company" : null;
}

export type InvoiceStatusLabel = InvoiceStatus;

/** Invoice status label key; `partial` displays as "Partly Paid". */
export function invoiceStatusKey(status: InvoiceStatus): string {
  if (status === "partial") return "ops.vocab.partlyPaid";
  return `status.${status}`;
}

/** Payment mode label key; `card` displays as "Credit Card". */
export function paymentModeKey(mode: "cash" | "card"): string {
  return mode === "card" ? "ops.vocab.creditCard" : "ops.v3.ceec253154";
}

export function poStatusKey(status: PurchaseOrder["status"]): string {
  if (status === "partlyReceived") return "status.partlyReceived";
  return `status.${status}`;
}

// ------------------------------------------------------------------- invoices
/**
 * The invoices a cashier can see: the submitted set plus the authored draft and
 * cancelled samples. The cancelled sample carries a voided payment, so the
 * list has to show non-collectable documents too.
 */
export function allInvoices(): Invoice[] {
  return [...opsData.invoices, sampleDraftInvoice, sampleCancelledInvoice];
}

export function invoiceById(id: string): Invoice | null {
  return allInvoices().find((invoice) => invoice.id === id) ?? null;
}

/** Draft and cancelled documents are never collectable. */
export function eligibleInvoices(): Invoice[] {
  return allInvoices().filter(
    (invoice) => invoice.docstatus === 1 && invoice.status !== "cancelled",
  );
}

export function invoiceOutstanding(invoice: Invoice): number {
  return opsData.dueOf(invoice);
}

export function allocationsFor(invoiceId: string) {
  return opsData.paymentRows({}).filter((row) => row.invoiceId === invoiceId);
}

/** Authored refund requests, newest first, with their invoice resolved. */
export function refundRequestsFor(): Array<
  RefundRequest & { invoice: Invoice | null }
> {
  return refundRequests.map((request) => ({
    ...request,
    invoice: invoiceById(request.invoiceId),
  }));
}

export function voidedPaymentSample() {
  return sampleVoidedPayment;
}

export function shiftTotals(): CashierShiftTotals {
  return cashierShiftTotals();
}

/** Posted allocated receipts of one mode on one Bangkok business day. */
export function collectedOn(
  mode: "cash" | "card" | null,
  dayKey: string,
): number {
  return opsData
    .paymentRows({})
    .filter(
      (row) =>
        (!mode || row.mode === mode) && opsData.dates.dayKey(row.date) === dayKey,
    )
    .reduce((sum, row) => sum + row.amount, 0);
}

/**
 * Completed encounters without a linked invoice: the fixture's own unbilled
 * rows plus the authored checkout sample. The authored row is marked so the
 * screen can keep the sample label next to it.
 */
export function completedVisitsAwaitingBilling(): Array<{
  id: string;
  patientId: string;
  date: string;
  sample: boolean;
}> {
  const fixtureRows = coreData.visits
    .filter((visit) => visit.state === "completed" && !opsData.invoiceForVisit(visit.id))
    .map((visit) => ({
      id: visit.id,
      patientId: visit.patientId,
      date: visit.date,
      sample: false,
    }));
  const authored = checkoutSamples.missingInvoice.visit;
  return [
    ...fixtureRows,
    {
      id: authored.id,
      patientId: authored.patientId,
      date: authored.date,
      sample: true,
    },
  ];
}

// --------------------------------------------------------------------- stock
export interface StockScopeOption {
  readonly id: string;
  readonly labelKey: string;
}

/** Per-warehouse balance truth for an item (physical vs usable vs threshold). */
export function itemStockByWarehouse(itemId: string) {
  return opsData
    .stockRows()
    .filter((row) => row.itemId === itemId)
    .map((row) => ({ ...row, threshold: row.reorderLevel }));
}

export function itemTotals(itemId: string) {
  const rows = itemStockByWarehouse(itemId);
  return {
    onHand: rows.reduce((sum, row) => sum + row.onHand, 0),
    usable: rows.reduce((sum, row) => sum + row.usable, 0),
  };
}

export const EXPIRY_BUCKETS = ["expired", "7", "30", "90", "later"] as const;
export type ExpiryBucket = (typeof EXPIRY_BUCKETS)[number];

/**
 * Exclusive expiry buckets straight from the helper's `urgency` field: a lot
 * is counted once, in exactly one window, so the strip always sums to the row
 * count.
 */
export function expiryBucketCounts(
  rows: readonly ExpiryRow[],
): Record<ExpiryBucket, number> {
  const counts: Record<ExpiryBucket, number> = {
    expired: 0,
    "7": 0,
    "30": 0,
    "90": 0,
    later: 0,
  };
  for (const row of rows) {
    const bucket = row.urgency as ExpiryBucket;
    if (bucket in counts) counts[bucket] += 1;
  }
  return counts;
}

export function expiryRowsFor(
  warehouse: string,
  window: string,
): ExpiryRow[] {
  return opsData.expiryRows(warehouse === ALL_SCOPE ? undefined : warehouse, window);
}

// ---------------------------------------------------------------- purchasing
/** POs that can take a receipt: submitted/partial, with a supplier and pending qty. */
export function receivablePurchaseOrders(): Array<{
  po: PurchaseOrder;
  progress: PoProgress;
}> {
  const rows: Array<{ po: PurchaseOrder; progress: PoProgress }> = [];
  for (const po of opsData.purchaseOrders) {
    const progress = opsData.poProgress(po.id);
    if (!progress) continue;
    if (!["submitted", "partlyReceived"].includes(po.status)) continue;
    if (!po.supplierId) continue;
    if (progress.pending <= 0) continue;
    rows.push({ po, progress });
  }
  return rows;
}

export function poProgressOf(poId: string): PoProgress | null {
  return opsData.poProgress(poId);
}

export type PoStatusFilter =
  | "all"
  | "open"
  | "draft"
  | "submitted"
  | "partlyReceived"
  | "closed"
  | "cancelled";

export function purchaseOrdersFor(
  warehouse: string,
  status: PoStatusFilter,
  search: string,
): PurchaseOrder[] {
  const needle = search.trim().toLowerCase();
  return opsData.purchaseOrders.filter((po) => {
    const inWarehouse =
      warehouse === ALL_SCOPE ||
      po.warehouse === warehouse ||
      po.items.some((line) => line.warehouse === warehouse);
    if (!inWarehouse) return false;
    if (status === "open") {
      if (["closed", "cancelled"].includes(po.status)) return false;
    } else if (status !== "all" && po.status !== status) {
      return false;
    }
    if (!needle) return true;
    const haystack = `${po.id} ${supplierName(po.supplierId, "en")} ${supplierName(
      po.supplierId,
      "th",
    )}`.toLowerCase();
    return haystack.includes(needle);
  });
}

// ------------------------------------------------------------------ ledger
export type LedgerKind = "receipt" | "usage" | "transfer" | "reconciliation";

export interface LedgerRow {
  readonly id: string;
  readonly at: string;
  readonly kind: LedgerKind;
  readonly itemId: string;
  readonly batchId: string;
  readonly warehouse: string;
  /** Signed stock effect; `null` for a reconcile request (no stock change). */
  readonly qty: number | null;
  readonly counted: number | null;
  readonly ref: string;
  readonly sourceRef: string | null;
  readonly documentRef: string | null;
  readonly targetWarehouse: string | null;
  /** Request-only rows never edit a balance or the ledger. */
  readonly requestOnly: boolean;
}

/**
 * PHA-06 ledger view: the immutable sample ledger plus the authored transfer
 * pair and reconciliation requests, in one chronological (oldest-first) list.
 * The core ledger rows are untouched; the authored rows are additive and each
 * one names its document. Reconciliation stays a request, never a movement.
 */
export function ledgerView(): LedgerRow[] {
  const core: LedgerRow[] = opsData.movements.map((movement) => ({
    id: movement.id,
    at: movement.at,
    kind: movement.type,
    itemId: movement.itemId,
    batchId: movement.batchId,
    warehouse: movement.warehouse,
    qty: movement.qty,
    counted: null,
    ref: movement.ref,
    sourceRef: movement.sourceRef ?? null,
    documentRef: movement.documentRef ?? null,
    targetWarehouse: movement.targetWarehouse ?? null,
    requestOnly: false,
  }));

  const transfers: LedgerRow[] = sampleTransfers.map((row) => ({
    id: row.id,
    at: row.at,
    kind: "transfer",
    itemId: row.itemId,
    batchId: row.batchId,
    warehouse: row.warehouse,
    qty: row.qty,
    counted: null,
    ref: row.documentRef,
    sourceRef: row.sourceRef,
    documentRef: row.documentRef,
    targetWarehouse: row.targetWarehouse,
    requestOnly: false,
  }));

  const reconciliations: LedgerRow[] = reconciliationRequests.map((row) => {
    const batch = opsData.batchById(row.batchId);
    return {
      id: row.id,
      at: row.at,
      kind: "reconciliation" as const,
      itemId: batch?.itemId ?? row.batchId,
      batchId: row.batchId,
      warehouse: row.warehouse,
      qty: null,
      counted: row.counted,
      ref: row.id,
      sourceRef: null,
      documentRef: null,
      targetWarehouse: null,
      requestOnly: true,
    };
  });

  return [...core, ...transfers, ...reconciliations].sort((a, b) => {
    if (a.at === b.at) return a.id < b.id ? -1 : 1;
    return a.at < b.at ? -1 : 1;
  });
}
