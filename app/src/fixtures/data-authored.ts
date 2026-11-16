/**
 * Demo/Sample fixtures — part 4: authored operations/admin gaps.
 *
 * Designed-but-unbacked components the prototype never populated:
 *  - cashier shift, draft/cancelled invoices, a voided payment, discount
 *    permission and refund-approval samples (CAS-01–03),
 *  - confirmed-revenue series, exports and PO history (MGR-01–02),
 *  - permission matrix, practitioner/lab-provider masters and stock-threshold
 *    settings (ADM-02–04),
 *  - transfer movement pair and reconciliation requests (PHA-06),
 *  - missing-invoice / incomplete-visit checkout samples (REC-05) and the
 *    return-visit source (REC-04).
 *
 * Money values are recomputed with `invoiceTotals` from the source lines; the
 * confirmed-revenue series is derived from appointment types, never stored.
 * Shapes use Frappe/ERPNext vocabulary where the installed model exists
 * (Purchase Order, Stock Entry, Stock Reconciliation, Item Reorder, User) and
 * are explicitly marked `(sample extension)` where the prototype has no model.
 *
 * Backend field mapping (TS → installed field):
 *   shift     → POS Opening Entry: user, period_start_date, opening_balance,
 *               cash_account/card_account (extension), company
 *   invoices  → Sales Invoice: customer/patient, encounter, posting_date,
 *               items[item_code, qty, rate], discount_amount, total_taxes,
 *               grand_total, outstanding_amount, docstatus, company
 *   payments  → Payment Entry: party, reference_name (invoice), posting_date,
 *               mode_of_payment, paid_amount, reference_no, docstatus, company
 *   refund    → Payment Refund Request (extension): invoice, payment, amount,
 *               reason, requested_by, status
 *   PO history→ Purchase Order + submitted Purchase Receipt progress
 *   transfers → Stock Entry (Material Transfer): item_code, batch_no,
 *               source_warehouse, target_warehouse, qty, transfer_reason
 *   reconcile → Stock Reconciliation: item_code, batch_no, warehouse,
 *               qty (counted), reason; request only, no stock change
 *   ADM       → Role Permission: parent (doctype), role, read/write/create/
 *               submit/cancel/amend, permlevel; Healthcare Practitioner:
 *               practitioner_name, registration_no, department, service_unit;
 *               Item Reorder: item_code, warehouse, reorder_level
 */

import type { Visit } from "./data";
import { coreData } from "./data";
import type { Invoice, InvoiceLine, RevenueDay } from "./data-ops";
import {
  COMPANY_ID,
  invoiceTotals,
  opsData,
  recentDayKeys,
} from "./data-ops";

export const SAMPLE_LABEL =
  "Demo/Sample operations data — fictional cashier, management and admin rows; not a Marley backend contract";

const dates = opsData.dates;
const today = dates.todayKey();

export const company = opsData.company;

// ------------------------------------------------------------------ cashier
export interface CashierShift {
  readonly id: string;
  readonly docType: "POS Opening Entry";
  readonly cashier: string;
  readonly companyId: string;
  readonly cashAccount: string;
  readonly cardAccount: string;
  readonly periodStartDate: string;
  readonly openingFloat: number;
  readonly status: "open";
  readonly sample: true;
}

export const cashierShift: CashierShift = {
  id: "SAMPLE-SHIFT-01",
  docType: "POS Opening Entry",
  cashier: "preeya",
  companyId: COMPANY_ID,
  cashAccount: "DC-CASH-DEMO",
  cardAccount: "DC-CARD-DEMO",
  periodStartDate: dates.at(0, "08:30"),
  openingFloat: 5000,
  status: "open",
  sample: true,
};

export interface CashierShiftTotals {
  readonly cash: number;
  readonly card: number;
  readonly total: number;
  readonly paymentCount: number;
}

/** Shift totals are derived from the posted payments on the shift day. */
export function cashierShiftTotals(shift: CashierShift = cashierShift): CashierShiftTotals {
  const shiftDay = opsData.dates.dayKey(shift.periodStartDate);
  const rows = opsData.payments.filter(
    (payment) =>
      payment.posted &&
      opsData.dates.dayKey(payment.date) === shiftDay,
  );
  const cash = rows
    .filter((payment) => payment.mode === "cash")
    .reduce((sum, payment) => sum + payment.amount, 0);
  const card = rows
    .filter((payment) => payment.mode === "card")
    .reduce((sum, payment) => sum + payment.amount, 0);
  return { cash, card, total: cash + card, paymentCount: rows.length };
}

function buildInvoice(input: {
  id: string;
  docstatus: 0 | 1 | 2;
  status: Invoice["status"];
  patientId: string;
  visitId: string;
  date: string;
  lines: readonly InvoiceLine[];
  discountRate?: number;
}): Invoice {
  const totals = invoiceTotals(input.lines, input.discountRate ?? 0);
  return {
    id: input.id,
    docType: "Sales Invoice",
    docstatus: input.docstatus,
    patientId: input.patientId,
    visitId: input.visitId,
    date: input.date,
    lines: input.lines,
    ...totals,
    paid: 0,
    status: input.status,
    dueDate: dates.addDays(input.date, 7),
    companyId: COMPANY_ID,
    sample: true,
  };
}

export const sampleDraftInvoice: Invoice = buildInvoice({
  id: "SAMPLE-INV-DRAFT-01",
  docstatus: 0,
  status: "draft",
  patientId: "P-10246",
  visitId: "SAMPLE-VIS-CHECKOUT-01",
  date: dates.at(0, "13:10"),
  lines: [{ itemId: "IT-23", desc: null, qty: 1, rate: 800 }],
});

export const sampleCancelledInvoice: Invoice = buildInvoice({
  id: "SAMPLE-INV-CANCELLED-01",
  docstatus: 2,
  status: "cancelled",
  patientId: "P-10247",
  visitId: "SAMPLE-VIS-INCOMPLETE-01",
  date: dates.at(-2, "15:20"),
  lines: [
    { itemId: "IT-23", desc: null, qty: 1, rate: 800 },
    { itemId: "IT-19", desc: null, qty: 1, rate: 550 },
  ],
  discountRate: 0.1,
});

export interface VoidedPayment {
  readonly id: string;
  readonly docType: "Payment Entry";
  readonly invoiceId: string;
  readonly patientId: string;
  readonly date: string;
  readonly mode: "cash" | "card";
  readonly amount: number;
  readonly reference: string;
  readonly posted: false;
  readonly status: "cancelled";
  readonly cancellationReason: string;
  readonly companyId: string;
  readonly sample: true;
}

export const sampleVoidedPayment: VoidedPayment = {
  id: "SAMPLE-PAY-VOID-01",
  docType: "Payment Entry",
  invoiceId: sampleCancelledInvoice.id,
  patientId: sampleCancelledInvoice.patientId,
  date: dates.at(-2, "15:35"),
  mode: "card",
  amount: 500,
  reference: "VOID-TERM-0091",
  posted: false,
  status: "cancelled",
  cancellationReason: "ยกเลิกรับชำระหลังยกเลิกใบแจ้งหนี้",
  companyId: COMPANY_ID,
  sample: true,
};

export interface DiscountPolicy {
  readonly id: string;
  readonly docType: "Discount Policy (sample extension)";
  readonly maxPercentWithoutApproval: number;
  readonly approverRoles: readonly string[];
  readonly requiresReason: boolean;
  readonly sample: true;
}

export const discountPolicy: DiscountPolicy = {
  id: "SAMPLE-DISCOUNT-POLICY",
  docType: "Discount Policy (sample extension)",
  maxPercentWithoutApproval: 10,
  approverRoles: ["MGR"],
  requiresReason: true,
  sample: true,
};

export interface DiscountApprovalSample {
  readonly id: string;
  readonly docType: "Discount Approval Request (sample extension)";
  readonly invoiceId: string;
  readonly requestedPercent: number;
  readonly reason: string;
  readonly requestedBy: string;
  readonly at: string;
  readonly status: "pending-manager-approval";
  readonly sample: true;
}

export const discountApprovalSample: DiscountApprovalSample = {
  id: "SAMPLE-DISCOUNT-REQ-01",
  docType: "Discount Approval Request (sample extension)",
  invoiceId: sampleDraftInvoice.id,
  requestedPercent: 15,
  reason: "ส่วนลดลูกค้าประจำตามนโยบายผู้จัดการ",
  requestedBy: "preeya",
  at: dates.at(0, "13:20"),
  status: "pending-manager-approval",
  sample: true,
};

const paidInvoice =
  opsData.invoices.find((invoice) => invoice.paid > 0) ?? sampleDraftInvoice;

export interface RefundRequest {
  readonly id: string;
  readonly docType: "Payment Refund Request (sample extension)";
  readonly invoiceId: string;
  readonly paymentId: string | null;
  readonly amount: number;
  readonly reason: string;
  readonly requestedBy: string;
  readonly at: string;
  readonly status: "pending-manager-approval";
  readonly companyId: string;
  readonly sample: true;
}

export const refundRequests: RefundRequest[] = [
  {
    id: "SAMPLE-REFUND-01",
    docType: "Payment Refund Request (sample extension)",
    invoiceId: paidInvoice.id,
    paymentId: paidInvoice.paymentId ?? null,
    amount: Math.min(500, paidInvoice.paid),
    reason: "ผู้ป่วยยกเลิกบริการหลังชำระเงิน",
    requestedBy: "preeya",
    at: dates.at(-1, "16:45"),
    status: "pending-manager-approval",
    companyId: COMPANY_ID,
    sample: true,
  },
];

// --------------------------------------------------------------- management
/**
 * Confirmed revenue: appointment-type price for every appointment in the day
 * that is not cancelled/no-show. Derived, never stored.
 */
export function confirmedRevenueSeries(days = 7): Array<
  RevenueDay & { readonly confirmed: number }
> {
  return recentDayKeys(days).map((key) => {
    const rows = coreData.appointments.filter(
      (appointment) =>
        opsData.dates.dayKey(appointment.date) === key &&
        appointment.status !== "cancelled" &&
        appointment.status !== "noShow",
    );
    const confirmed = rows.reduce(
      (sum, appointment) =>
        sum + coreData.appointmentTypeById(appointment.typeId).price,
      0,
    );
    const invoiceRows = opsData.invoices.filter(
      (invoice) => invoice.date.slice(0, 10) === key,
    );
    const invoiced = invoiceRows.reduce(
      (sum, invoice) => sum + invoice.total,
      0,
    );
    const collected = opsData.payments
      .filter((payment) => payment.date.slice(0, 10) === key)
      .reduce((sum, payment) => sum + payment.amount, 0);
    return {
      key,
      invoiced,
      collected,
      invoiceCount: invoiceRows.length,
      confirmed,
    };
  });
}

export interface InventoryTrendDay {
  readonly key: string;
  readonly closingUnits: number;
  readonly closingValue: number;
  readonly inboundUnits: number;
  readonly outboundUnits: number;
}

/**
 * Inventory trend derived from the sample stock ledger movements (no stored
 * series): closing units/value as of each period day plus that day's inbound
 * and outbound movement. The authored transfer pair nets to zero and is not
 * part of the core ledger, so totals reconcile with `opsData.batches`.
 */
export function inventoryTrend(days = 7): InventoryTrendDay[] {
  const costByBatch = new Map(
    opsData.batches.map((batch) => [batch.id, batch.cost] as const),
  );
  const movementValue = (movement: { batchId: string; qty: number }) =>
    movement.qty * (costByBatch.get(movement.batchId) ?? 0);
  const dayOf = (movement: { at: string }) => opsData.dates.dayKey(movement.at);

  return recentDayKeys(days).map((key) => {
    const upTo = opsData.movements.filter((movement) => dayOf(movement) <= key);
    const todayRows = opsData.movements.filter(
      (movement) => dayOf(movement) === key,
    );
    return {
      key,
      closingUnits: upTo.reduce((sum, movement) => sum + movement.qty, 0),
      closingValue: upTo.reduce(
        (sum, movement) => sum + movementValue(movement),
        0,
      ),
      inboundUnits: todayRows
        .filter((movement) => movement.qty > 0)
        .reduce((sum, movement) => sum + movement.qty, 0),
      outboundUnits: todayRows
        .filter((movement) => movement.qty < 0)
        .reduce((sum, movement) => sum - movement.qty, 0),
    };
  });
}

export interface ReportExport {
  readonly id: string;
  readonly docType: "Report Export (sample)";
  readonly tab: "appointments" | "procedures" | "finance" | "inventory" | "purchasing";
  readonly label: { readonly th: string; readonly en: string };
  readonly format: "csv";
  readonly sample: true;
}

export const reportExports: ReportExport[] = [
  { id: "SAMPLE-EXP-APPOINTMENTS", docType: "Report Export (sample)", tab: "appointments", label: { th: "นัดหมาย", en: "Appointments" }, format: "csv", sample: true },
  { id: "SAMPLE-EXP-PROCEDURES", docType: "Report Export (sample)", tab: "procedures", label: { th: "รายการใบแจ้งหนี้", en: "Invoice items" }, format: "csv", sample: true },
  { id: "SAMPLE-EXP-FINANCE", docType: "Report Export (sample)", tab: "finance", label: { th: "การเงิน", en: "Finance" }, format: "csv", sample: true },
  { id: "SAMPLE-EXP-INVENTORY", docType: "Report Export (sample)", tab: "inventory", label: { th: "คลัง", en: "Inventory" }, format: "csv", sample: true },
  { id: "SAMPLE-EXP-PURCHASING", docType: "Report Export (sample)", tab: "purchasing", label: { th: "จัดซื้อ", en: "Purchasing" }, format: "csv", sample: true },
];

export interface PurchaseOrderHistoryRow {
  readonly id: string;
  readonly docType: "Purchase Order History (sample)";
  readonly poId: string;
  readonly supplierId: string;
  readonly status: string;
  readonly ordered: number;
  readonly received: number;
  readonly pending: number;
  readonly percent: number;
  readonly receiptIds: readonly string[];
  readonly sample: true;
}

/** PO history reuses the computed receipt progress for every sample PO. */
export function purchaseOrderHistory(): PurchaseOrderHistoryRow[] {
  return opsData.purchaseOrders.map((po) => {
    const progress = opsData.poProgress(po.id);
    return {
      id: `SAMPLE-PO-HIST-${po.id}`,
      docType: "Purchase Order History (sample)",
      poId: po.id,
      supplierId: po.supplierId,
      status: po.status,
      ordered: progress?.ordered ?? 0,
      received: progress?.received ?? 0,
      pending: progress?.pending ?? 0,
      percent: progress?.percent ?? 0,
      receiptIds: progress?.receiptIds ?? [],
      sample: true,
    };
  });
}

// -------------------------------------------------------------------- admin
export type MatrixRole = "REC" | "DOC" | "NUR" | "PHA" | "CAS" | "MGR" | "ADM";

export interface PermissionMatrixRow {
  readonly id: string;
  readonly docType: "Role Permission (sample)";
  readonly doctype: string;
  readonly role: MatrixRole;
  readonly read: boolean;
  readonly write: boolean;
  readonly create: boolean;
  readonly submit: boolean;
  readonly cancel: boolean;
  readonly amend: boolean;
  readonly permlevel: 0;
  /** The installed-site matrix is unverified; this is a presentation fixture. */
  readonly verified: false;
  readonly sample: true;
}

const permission = (
  doctype: string,
  role: MatrixRole,
  rights: Partial<Pick<PermissionMatrixRow, "read" | "write" | "create" | "submit" | "cancel" | "amend">>,
): PermissionMatrixRow => ({
  id: `SAMPLE-PERM-${doctype.replace(/\W+/g, "-")}-${role}`,
  docType: "Role Permission (sample)",
  doctype,
  role,
  read: rights.read ?? false,
  write: rights.write ?? false,
  create: rights.create ?? false,
  submit: rights.submit ?? false,
  cancel: rights.cancel ?? false,
  amend: rights.amend ?? false,
  permlevel: 0,
  verified: false,
  sample: true,
});

export const permissionMatrix: PermissionMatrixRow[] = [
  permission("Patient", "REC", { read: true, write: true, create: true }),
  permission("Patient", "DOC", { read: true, write: true }),
  permission("Patient", "NUR", { read: true }),
  permission("Patient", "ADM", { read: true, write: true, create: true }),
  permission("Patient Appointment", "REC", { read: true, write: true, create: true }),
  permission("Patient Appointment", "DOC", { read: true }),
  permission("Patient Appointment", "NUR", { read: true }),
  permission("Patient Encounter", "DOC", { read: true, write: true, create: true, submit: true, cancel: true, amend: true }),
  permission("Patient Encounter", "NUR", { read: true }),
  permission("Clinical Note", "DOC", { read: true, write: true, create: true, submit: true, amend: true }),
  permission("Clinic Photo", "DOC", { read: true, write: true, create: true }),
  permission("Clinic Photo", "NUR", { read: true, create: true }),
  permission("Healthcare Practitioner", "ADM", { read: true, write: true, create: true }),
  permission("Healthcare Practitioner", "MGR", { read: true }),
  permission("Item", "PHA", { read: true, write: true, create: true }),
  permission("Item", "ADM", { read: true, write: true }),
  permission("Batch", "PHA", { read: true, write: true, create: true }),
  permission("Purchase Order", "PHA", { read: true, write: true, create: true, submit: true, cancel: true }),
  permission("Purchase Receipt", "PHA", { read: true, write: true, create: true, submit: true }),
  permission("Sales Invoice", "CAS", { read: true, write: true, create: true, submit: true, cancel: true }),
  permission("Sales Invoice", "MGR", { read: true }),
  permission("Payment Entry", "CAS", { read: true, write: true, create: true, submit: true }),
  permission("Payment Entry", "MGR", { read: true }),
  permission("User", "ADM", { read: true, write: true, create: true }),
  permission("System Settings", "ADM", { read: true, write: true }),
];

export interface PractitionerMaster {
  readonly id: string;
  readonly docType: "Healthcare Practitioner";
  readonly name: { readonly th: string; readonly en: string };
  readonly registrationNo: string;
  readonly designation: { readonly th: string; readonly en: string };
  readonly department: { readonly th: string; readonly en: string };
  readonly serviceUnits: readonly string[];
  readonly active: boolean;
  readonly sample: true;
}

export const practitionerMasters: PractitionerMaster[] = coreData.practitioners.map(
  (practitioner, index) => ({
    id: practitioner.id,
    docType: "Healthcare Practitioner",
    name: practitioner.name,
    registrationNo: `SAMPLE-MD-${String(index + 1).padStart(4, "0")}`,
    designation: { th: "แพทย์", en: "Physician" },
    department: practitioner.dept,
    serviceUnits: index === 0 ? ["SU-01", "SU-02"] : index === 1 ? ["SU-03"] : ["SU-04"],
    active: true,
    sample: true,
  }),
);

export interface LabProviderMaster {
  readonly id: string;
  readonly docType: "Lab Provider (sample extension)";
  readonly name: { readonly th: string; readonly en: string };
  readonly contact: string;
  readonly phone: string;
  readonly externalRefPrefix: string;
  readonly active: boolean;
  readonly contractStatus: "sample-unverified";
  readonly lastVerifiedAt: null;
  readonly sample: true;
}

const LAB_PROVIDER_DETAILS: Record<
  string,
  { contact: string; phone: string; prefix: string }
> = {
  "LP-01": { contact: "คุณวราภรณ์", phone: "02-300-1122", prefix: "BCL" },
  "LP-02": { contact: "คุณปิยะ", phone: "02-201-4455", prefix: "RAM" },
  "LP-03": { contact: "คุณชัย", phone: "02-630-7788", prefix: "SLM" },
};

export const labProviderMasters: LabProviderMaster[] = opsData.labProviders.map(
  (provider) => {
    const detail = LAB_PROVIDER_DETAILS[provider.id] ?? {
      contact: "—",
      phone: "—",
      prefix: "LAB",
    };
    return {
      id: provider.id,
      docType: "Lab Provider (sample extension)",
      name: provider.name,
      contact: detail.contact,
      phone: detail.phone,
      externalRefPrefix: detail.prefix,
      active: provider.active,
      contractStatus: "sample-unverified",
      lastVerifiedAt: null,
      sample: true,
    };
  },
);

export interface StockThresholdSetting {
  readonly id: string;
  readonly docType: "Item Reorder";
  readonly itemId: string;
  readonly warehouse: string;
  readonly reorderLevel: number;
  readonly editable: true;
  readonly updatedBy: string;
  readonly updatedAt: string;
  readonly sample: true;
}

export const stockThresholdSettings: StockThresholdSetting[] =
  opsData.stockThresholds.map((threshold) => ({
    id: threshold.id.replace("SAMPLE-REORDER", "SAMPLE-THRESHOLD"),
    docType: "Item Reorder",
    itemId: threshold.itemId,
    warehouse: threshold.warehouse,
    reorderLevel: threshold.reorderLevel,
    editable: true,
    updatedBy: "naphatsorn",
    updatedAt: dates.at(-7, "10:00"),
    sample: true,
  }));

export interface StockThresholdPolicy {
  readonly id: string;
  readonly docType: "Stock Settings (sample extension)";
  readonly policy: "per-item-location";
  readonly retailUnconfigured: true;
  readonly note: { readonly th: string; readonly en: string };
  readonly sample: true;
}

export const stockThresholdPolicy: StockThresholdPolicy = {
  id: "SAMPLE-STOCK-POLICY",
  docType: "Stock Settings (sample extension)",
  policy: "per-item-location",
  retailUnconfigured: true,
  note: {
    th: "จุดสั่งซื้อเป็นรายสินค้าและคลัง คลังหน้าร้านยังไม่ตั้งค่าโดยเจตนา",
    en: "Reorder points are per item and warehouse; the retail shelf is intentionally unconfigured",
  },
  sample: true,
};

// --------------------------------------------------------- transfers/reconcile
const transferBatch =
  opsData.batches.find(
    (batch) => batch.warehouse === "WH-MAIN" && batch.qty >= 3,
  ) ?? opsData.batches.find((batch) => batch.qty >= 3);

if (!transferBatch) {
  throw new Error("sample transfer needs a stocked batch of at least 3 units");
}

const TRANSFER_QTY = 2;
const TRANSFER_TARGET = "WH-ROOM";

export interface TransferMovement {
  readonly id: string;
  readonly docType: "Stock Entry";
  readonly at: string;
  readonly itemId: string;
  readonly batchId: string;
  readonly warehouse: string;
  readonly qty: number;
  readonly type: "transfer";
  readonly targetWarehouse: string;
  readonly reason: "replenishment";
  readonly sourceRef: string;
  readonly documentRef: string;
  readonly sample: true;
}

/** Paired two-sided transfer: the two rows net to zero and never exceed stock. */
export const sampleTransfers: TransferMovement[] = [
  {
    id: "SAMPLE-STE-9001-OUT",
    docType: "Stock Entry",
    at: dates.at(-1, "11:00"),
    itemId: transferBatch.itemId,
    batchId: transferBatch.id,
    warehouse: transferBatch.warehouse,
    qty: -TRANSFER_QTY,
    type: "transfer",
    targetWarehouse: TRANSFER_TARGET,
    reason: "replenishment",
    sourceRef: "SAMPLE-PR-001",
    documentRef: "SAMPLE-STE-9001",
    sample: true,
  },
  {
    id: "SAMPLE-STE-9001-IN",
    docType: "Stock Entry",
    at: dates.at(-1, "11:00"),
    itemId: transferBatch.itemId,
    batchId: transferBatch.id,
    warehouse: TRANSFER_TARGET,
    qty: TRANSFER_QTY,
    type: "transfer",
    targetWarehouse: TRANSFER_TARGET,
    reason: "replenishment",
    sourceRef: "SAMPLE-PR-001",
    documentRef: "SAMPLE-STE-9001",
    sample: true,
  },
];

const reconcileBatch =
  opsData.batches.find(
    (batch) => batch.id !== transferBatch.id && batch.qty > 0,
  ) ?? transferBatch;

export interface ReconciliationRequest {
  readonly id: string;
  readonly docType: "Stock Reconciliation";
  readonly batchId: string;
  readonly warehouse: string;
  readonly counted: number;
  readonly reason: "count-discrepancy";
  readonly status: "pending-verification";
  readonly noStockChange: true;
  readonly requestedBy: string;
  readonly at: string;
  readonly sample: true;
}

export const reconciliationRequests: ReconciliationRequest[] = [
  {
    id: "SAMPLE-SRECO-01",
    docType: "Stock Reconciliation",
    batchId: reconcileBatch.id,
    warehouse: reconcileBatch.warehouse,
    counted: Math.max(0, reconcileBatch.qty - 1),
    reason: "count-discrepancy",
    status: "pending-verification",
    noStockChange: true,
    requestedBy: "thanapon",
    at: dates.at(-1, "15:30"),
    sample: true,
  },
];

// ----------------------------------------------------------------- checkout
function sampleVisit(input: {
  id: string;
  patientId: string;
  state: "draft" | "completed";
  date: string;
}): Visit {
  return {
    id: input.id,
    docType: "Patient Encounter",
    patientId: input.patientId,
    appointmentId: null,
    practitioner: "HP-01",
    date: input.date,
    state: input.state,
    history: {
      complaint: "ตัวอย่างบริบทเช็คเอาท์สำหรับหน้าจอ Demo/Sample",
      reviewedAllergies: true,
    },
    exam: { area: "face" },
    note:
      input.state === "completed"
        ? { signed: true, assessment: "ตัวอย่างการตรวจที่ยังไม่ออกใบแจ้งหนี้" }
        : { signed: false },
    diagnoses: [],
    meds: [],
    sample: true,
  };
}

export interface CheckoutSamples {
  readonly missingInvoice: {
    readonly visit: Visit;
    readonly expectedInvoice: null;
    readonly note: { readonly th: string; readonly en: string };
  };
  readonly incompleteVisit: {
    readonly visit: Visit;
    readonly gaps: readonly string[];
    readonly note: { readonly th: string; readonly en: string };
  };
}

export const checkoutSamples: CheckoutSamples = {
  missingInvoice: {
    visit: sampleVisit({
      id: "SAMPLE-VIS-CHECKOUT-01",
      patientId: "P-10246",
      state: "completed",
      date: dates.at(0, "13:05"),
    }),
    expectedInvoice: null,
    note: {
      th: "การตรวจเสร็จแล้วแต่ยังไม่มีใบแจ้งหนี้ — ตัวอย่างสำหรับ REC-05",
      en: "Visit completed without an invoice — REC-05 sample",
    },
  },
  incompleteVisit: {
    visit: sampleVisit({
      id: "SAMPLE-VIS-INCOMPLETE-01",
      patientId: "P-10247",
      state: "draft",
      date: dates.at(0, "13:40"),
    }),
    gaps: ["note.assessment", "note.plan", "procedures"],
    note: {
      th: "การตรวจยังไม่สมบูรณ์ก่อนเช็คเอาท์ — ตัวอย่างสำหรับ REC-05",
      en: "Visit is not complete at checkout — REC-05 sample",
    },
  },
};

export interface ReturnVisitSource {
  readonly id: string;
  readonly docType: "Patient Appointment Request (sample)";
  readonly source: "REC-04";
  readonly patientId: string;
  readonly sourceAppointmentId: string;
  readonly lastVisitId: string;
  readonly suggestedTypeId: string;
  readonly requestedAt: string;
  readonly note: { readonly th: string; readonly en: string };
  readonly sample: true;
}

export const returnVisitSource: ReturnVisitSource = {
  id: "SAMPLE-RETURN-01",
  docType: "Patient Appointment Request (sample)",
  source: "REC-04",
  patientId: "P-10241",
  sourceAppointmentId: "DC-APT-3301",
  lastVisitId: "DC-VIS-2041",
  suggestedTypeId: "AT-02",
  requestedAt: dates.at(0, "14:30"),
  note: {
    th: "นัดติดตามจากหน้าจอการนัดหมาย — เปิดฟอร์มจองพร้อมแหล่งที่มากลับมาพบ",
    en: "Follow-up from the appointment screen — opens booking with return-visit source",
  },
  sample: true,
};

export const authoredOps = {
  company,
  cashierShift,
  sampleDraftInvoice,
  sampleCancelledInvoice,
  sampleVoidedPayment,
  discountPolicy,
  discountApprovalSample,
  refundRequests,
  reportExports,
  inventoryTrend,
  permissionMatrix,
  practitionerMasters,
  labProviderMasters,
  stockThresholdSettings,
  stockThresholdPolicy,
  sampleTransfers,
  reconciliationRequests,
  checkoutSamples,
  returnVisitSource,
} as const;

// Keep the frozen day referenced so the authored rows always state which day
// they were anchored to (mirrors `data.ts`'s single-clock rule).
export const AUTHORED_DAY = today;
