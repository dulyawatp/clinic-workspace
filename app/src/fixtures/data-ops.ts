/**
 * Demo/Sample fixtures — part 2: stock, purchasing, billing, staff, masters.
 *
 * Ported from the prototype's `assets/js/data-ops.js` (LCG seed 778899) and
 * its v3 helper layer. The rule from the gap audit is enforced throughout:
 * **computed values are recomputed, never stored as truth** — reorder
 * thresholds, PO progress, due balances, stock usability, VAT/discount/paid
 * and KPI series are derived from the source rows at read time.
 *
 * Fixes against the prototype (see the UI-parity gap audit):
 *  - the stock ledger is chronological (`LEDGER_ORDER`, oldest first) and each
 *    lot's movements sum to its stored balance;
 *  - receipt DC-PR-0019 now resolves to a real sample PO/lot instead of
 *    dangling against the absent DC-PO-0106;
 *  - purchase orders carry their inventory warehouse;
 *  - invoices and payments carry the sample company;
 *  - staff rows keep the canonical role code and list the Frappe role names
 *    beside it instead of overwriting the role vocabulary;
 *  - schedules expose ISO weekday numbers instead of raw day codes only.
 *
 * Frappe DocTypes modelled: Warehouse, Item, Batch, Stock Ledger Entry,
 * Item Reorder, Supplier, Purchase Order, Purchase Receipt, Sales Invoice,
 * Payment Entry, User, Healthcare Practitioner schedule, Clinical Procedure
 * Template, Lab Test Template. Everything is `sample: true`.
 */

import type { CoreData, L10n, Visit } from "./data";
import { coreData, clinicDates, seededRng } from "./data";

export { seededRng };

export const SAMPLE_LABEL =
  "Demo/Sample data — fictional stock, billing and admin rows; not a Marley backend contract";

export const SEED_OPS = 778899;

/** Immutable ledger reads oldest-first; UIs may reverse for display. */
export const LEDGER_ORDER = "oldest-first" as const;

export const COMPANY_ID = "Demo Clinic (DC)";

export interface Warehouse {
  readonly id: string;
  readonly docType: "Warehouse";
  readonly name: L10n;
  readonly sample: true;
}

export type ItemGroup = "MED" | "CON" | "SUP" | "RTL" | "SRV";

export interface Item {
  readonly id: string;
  readonly docType: "Item";
  readonly code: string;
  readonly name: L10n;
  readonly group: ItemGroup;
  readonly uom: string;
  readonly price: number;
  readonly reorder: number;
  readonly isStock: boolean;
  readonly batchTracked: boolean;
  readonly sample: true;
}

export interface Batch {
  readonly id: string;
  readonly docType: "Batch";
  readonly batchId: string;
  readonly itemId: string;
  readonly warehouse: string;
  readonly expiry: string;
  readonly qty: number;
  readonly cost: number;
  readonly sample: true;
}

export type MovementType = "receipt" | "transfer" | "usage";

export interface Movement {
  readonly id: string;
  readonly docType: "Stock Ledger Entry";
  readonly at: string;
  readonly itemId: string;
  readonly batchId: string;
  readonly warehouse: string;
  readonly qty: number;
  readonly type: MovementType;
  readonly ref: string;
  readonly targetWarehouse?: string;
  readonly reason?: string;
  readonly sourceRef?: string;
  readonly documentRef?: string;
  readonly sample: true;
}

export interface StockThreshold {
  readonly id: string;
  readonly docType: "Item Reorder";
  readonly itemId: string;
  readonly warehouse: string;
  readonly reorderLevel: number;
  readonly source: "sample";
  readonly sample: true;
}

export interface Supplier {
  readonly id: string;
  readonly docType: "Supplier";
  readonly name: L10n;
  readonly phone: string;
  readonly contact: string;
  readonly terms: string;
  readonly sample: true;
}

export interface PurchaseOrderLine {
  readonly itemId: string;
  readonly qty: number;
  readonly received: number;
  readonly rate: number;
  readonly warehouse: string;
}

export type PurchaseOrderStatus =
  | "draft"
  | "submitted"
  | "partlyReceived"
  | "closed"
  | "cancelled";

export interface PurchaseOrder {
  readonly id: string;
  readonly docType: "Purchase Order";
  readonly supplierId: string;
  readonly warehouse: string;
  readonly date: string;
  readonly required: string;
  readonly status: PurchaseOrderStatus;
  readonly items: readonly PurchaseOrderLine[];
  readonly sample: true;
}

export interface ReceiptLine {
  readonly itemId: string | null;
  readonly qty: number;
  readonly value?: number;
  readonly lineIndex?: number;
  readonly unmapped?: true;
}

export interface PurchaseReceipt {
  readonly id: string;
  readonly docType: "Purchase Receipt";
  readonly poId: string;
  readonly supplierId?: string;
  readonly date: string;
  readonly status: "submitted" | "draft" | "cancelled";
  readonly value: number;
  readonly items: number;
  readonly by: string;
  readonly lines: readonly ReceiptLine[];
  readonly sample: true;
}

export interface InvoiceLine {
  readonly itemId: string;
  readonly desc: L10n | null;
  readonly qty: number;
  readonly rate: number;
}

export type InvoiceStatus =
  | "draft"
  | "unpaid"
  | "partial"
  | "paid"
  | "cancelled";

export interface Invoice {
  readonly id: string;
  readonly docType: "Sales Invoice";
  /** Frappe docstatus: 0 draft, 1 submitted, 2 cancelled. */
  readonly docstatus: 0 | 1 | 2;
  readonly patientId: string;
  readonly visitId: string | null;
  readonly date: string;
  readonly lines: readonly InvoiceLine[];
  readonly subtotal: number;
  readonly discount: number;
  readonly vat: number;
  readonly total: number;
  readonly paid: number;
  readonly status: InvoiceStatus;
  readonly dueDate: string;
  readonly companyId: string;
  readonly paymentId?: string;
  readonly sample: true;
}

export interface Payment {
  readonly id: string;
  readonly docType: "Payment Entry";
  readonly invoiceId: string;
  readonly patientId: string;
  readonly date: string;
  readonly mode: "cash" | "card";
  readonly amount: number;
  readonly receivedBy: string;
  readonly reference: string;
  readonly posted: boolean;
  readonly companyId: string;
  readonly status?: "posted" | "cancelled";
  readonly sample: true;
}

export type RoleCodeLike = "REC" | "DOC" | "NUR" | "PHA" | "CAS" | "MGR" | "ADM";

export interface StaffMember {
  readonly id: string;
  readonly docType: "User";
  readonly name: L10n;
  /** Canonical design role code; never overwritten by a Frappe role name. */
  readonly role: RoleCodeLike;
  /** Frappe/Marley role names carried beside the canonical code. */
  readonly frappeRoles: readonly string[];
  readonly user: string;
  readonly lang: "th" | "en";
  readonly active: boolean;
  readonly practitioner: string | null;
  readonly lastLogin: string;
  readonly sample: true;
}

export type DayCode = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";

export interface Schedule {
  readonly practitioner: string;
  readonly docType: "Practitioner Schedule (sample)";
  readonly day: DayCode;
  /** ISO weekday: 1 = Monday … 7 = Sunday. */
  readonly weekday: number;
  /** Catalogue key a UI can render instead of the raw day code. */
  readonly dayLabelKey: "appointment.day";
  readonly start: string;
  readonly end: string;
  readonly room: string;
  readonly sample: true;
}

export interface ProcedureTemplate {
  readonly id: string;
  readonly docType: "Clinical Procedure Template";
  readonly name: L10n;
  readonly minutes: number;
  readonly price: number;
  readonly consumables: readonly string[];
  readonly room: string;
  readonly sample: true;
}

export interface LabTemplate {
  readonly id: string;
  readonly docType: "Lab Test Template";
  readonly name: L10n;
  readonly sampleKind: L10n;
  readonly sample: true;
}

export interface LabProvider {
  readonly id: string;
  readonly docType: "Lab Provider (sample extension)";
  readonly name: L10n;
  readonly active: boolean;
  readonly sample: true;
}

export type ExternalLabStatus =
  | "ordered"
  | "collected"
  | "sent"
  | "received"
  | "reviewed"
  | "cancelled";

export interface ExternalLab {
  readonly id: string;
  readonly docType: "External Lab Request (sample extension)";
  readonly patientId: string;
  readonly visitId: string | null;
  readonly practitioner: string;
  readonly test: L10n;
  readonly provider: string;
  readonly status: ExternalLabStatus;
  readonly orderedAt: string;
  readonly sentAt: string | null;
  readonly sentBy: string | null;
  readonly receivedAt: string | null;
  readonly receivedBy: string | null;
  readonly reviewedAt: string | null;
  readonly reviewedBy: string | null;
  readonly file: string | null;
  readonly sampleKind: string;
  readonly externalRef: string | null;
  readonly interpretation?: string;
  readonly sample: true;
}

export interface AuditEntry {
  readonly id: string;
  readonly docType: "Activity Log";
  readonly at: string;
  readonly actor: string;
  readonly action: L10n;
  readonly entity: string;
  readonly note: L10n;
  readonly sample: true;
}

export interface SystemCheck {
  readonly id: string;
  readonly docType: "System Check (sample)";
  readonly name: L10n;
  readonly status: "unverified" | "extension" | "decision";
  readonly checkedAt: string | null;
  readonly note: L10n;
  readonly sample: true;
}

export interface Company {
  readonly id: string;
  readonly docType: "Company";
  readonly name: L10n;
  readonly currency: "THB";
  readonly country: "Thailand";
  readonly sample: true;
}

export interface PaymentRow {
  readonly id: string;
  readonly invoiceId: string;
  readonly patientId: string;
  readonly date: string;
  readonly mode: "cash" | "card";
  readonly amount: number;
  readonly posted: boolean;
  readonly reference: string | null;
  readonly receivedBy: string | null;
  readonly invoiceDate: string | null;
  readonly company: string;
}

export interface PoProgressLine {
  readonly lineIndex: number;
  readonly poLineId: string;
  readonly itemId: string;
  readonly ordered: number;
  readonly received: number;
  readonly pending: number;
  readonly overReceived: number;
}

export interface PoProgress {
  readonly ordered: number;
  readonly received: number;
  readonly pending: number;
  readonly overReceived: number;
  readonly percent: number;
  readonly lines: PoProgressLine[];
  readonly receiptIds: string[];
  readonly unmatchedLines: Array<{
    receiptId: string;
    lineIndex: number | null;
    itemId: string | null;
    qty: number;
    reason: string;
  }>;
}

export interface StockRow {
  readonly itemId: string;
  readonly warehouse: string;
  readonly onHand: number;
  readonly usable: number;
  readonly reorderLevel: number | null;
  readonly low: boolean;
  readonly out: boolean;
  readonly needed: number | null;
}

export interface ExpiryRow {
  readonly id: string;
  readonly batchId: string;
  readonly itemId: string;
  readonly warehouse: string;
  readonly expiry: string;
  readonly onHand: number;
  readonly cost: number;
  readonly daysRemaining: number | null;
  readonly usableQty: number;
  readonly urgency: string;
}

export interface HoursConfig {
  timezone: string;
  clinic: Array<{ day: DayCode; start: string; end: string }>;
  breaks: Array<{ practitioner: string; day: DayCode; start: string; end: string }>;
  leave: Array<{ practitioner: string; date: string; start: string; end: string }>;
}

export interface AvailabilityResult {
  readonly available: boolean;
  readonly reason: string | null;
  readonly source: string;
}

export interface MetricsResult {
  readonly appointments: number;
  readonly checkedIn: number;
  readonly completed: number;
  readonly invoiced: number;
  readonly collected: number;
  readonly outstanding: number;
  readonly lowStock: number;
  readonly expiring: number;
  readonly appointmentRows: CoreData["appointments"];
  readonly invoiceRows: Invoice[];
  readonly paymentRows: PaymentRow[];
  readonly outstandingRows: Array<Invoice & { due: number }>;
  readonly stockRows: StockRow[];
  readonly expiryRows: ExpiryRow[];
}

export interface TotalsResult {
  readonly invoiced: number;
  readonly collected: number;
  readonly outstanding: number;
  readonly invoiceCount: number;
  readonly appointments: number;
  readonly seen: number;
  readonly cancelled: number;
  readonly procedures: number;
  readonly inventoryValue: number;
  readonly expiringCount: number;
  readonly expiredCount: number;
  readonly lowStock: number;
  readonly outOfStock: number;
}

export interface RevenueDay {
  readonly key: string;
  readonly invoiced: number;
  readonly collected: number;
  readonly invoiceCount: number;
}

export interface PaymentFilters {
  readonly period?: "today" | "week" | "month" | "all";
  readonly anchor?: string;
  readonly mode?: string;
  readonly company?: string;
}

export interface OpsData {
  readonly now: string;
  readonly dates: ReturnType<typeof clinicDates>;
  readonly warehouses: Warehouse[];
  readonly items: Item[];
  readonly batches: Batch[];
  readonly movements: Movement[];
  readonly stockThresholds: StockThreshold[];
  readonly suppliers: Supplier[];
  readonly purchaseOrders: PurchaseOrder[];
  readonly receipts: PurchaseReceipt[];
  readonly invoices: Invoice[];
  readonly payments: Payment[];
  readonly staff: StaffMember[];
  readonly schedules: Schedule[];
  readonly procedureTemplates: ProcedureTemplate[];
  readonly labTemplates: LabTemplate[];
  readonly labProviders: LabProvider[];
  readonly externalLabs: ExternalLab[];
  readonly audit: AuditEntry[];
  readonly systemChecks: SystemCheck[];
  readonly company: Company;
  readonly counts: CoreData["counts"];
  readonly itemById: (id: string) => Item | null;
  readonly batchById: (id: string) => Batch | null;
  readonly invoiceById: (id: string) => Invoice | null;
  readonly paymentById: (id: string) => Payment | null;
  readonly supplierById: (id: string) => Supplier;
  readonly invoiceForVisit: (visitId: string) => Invoice | null;
  readonly invoicesFor: (patientId: string) => Invoice[];
  readonly paymentsForInvoice: (invoiceId: string) => Payment[];
  readonly stockOnHand: (itemId: string, warehouse?: string) => number;
  readonly stockStatus: (itemId: string) => "out" | "low" | "ok";
  readonly batchStatus: (batch: Batch) => "expired" | "expiring" | "ok";
  readonly daysToExpiry: (iso: string) => number;
  readonly resolveConsumableBatch: (
    itemId: string,
    warehouse: string,
  ) => Batch | null;
  readonly poProgress: (poId: string) => PoProgress | null;
  readonly paymentRows: (filters?: PaymentFilters) => PaymentRow[];
  readonly dueOf: (invoice: Invoice) => number;
  readonly outstandingRows: () => Array<Invoice & { due: number }>;
  readonly appointmentsByStatus: (dayKeyValue?: string) => Record<string, number>;
  readonly doctorWorkload: (days?: number) => Array<{
    practitioner: string;
    count: number;
  }>;
  readonly revenueByDay: (days?: number) => RevenueDay[];
  readonly totals: (days?: number) => TotalsResult;
  readonly topServices: (days?: number) => Array<{
    itemId: string;
    qty: number;
    amount: number;
  }>;
  readonly metrics: (
    period: "today" | "week" | "month",
    options?: { anchor?: string; warehouse?: string; practitioner?: string },
  ) => MetricsResult;
  readonly expiryRows: (warehouse?: string, window?: string) => ExpiryRow[];
  readonly stockRows: (warehouse?: string) => StockRow[];
  readonly expiryDays: (expiry: string, asOf?: string) => number | null;
  readonly availability: (options: {
    practitioner?: string;
    date?: string;
    time?: string;
    minutes?: number;
    excludeId?: string;
    room?: string;
  }) => AvailabilityResult;
  readonly validateHours: (config: unknown) => { ok: boolean; error?: string };
  readonly hours: () => HoursConfig;
}

/** VAT/discount totals for a line set — shared by the fixture authors. */
export function invoiceTotals(
  lines: readonly InvoiceLine[],
  discountRate = 0,
): { subtotal: number; discount: number; vat: number; total: number } {
  const subtotal = lines.reduce((sum, line) => sum + line.qty * line.rate, 0);
  const discount = Math.round(subtotal * discountRate);
  const vat = Math.round((subtotal - discount) * 0.07);
  return { subtotal, discount, vat, total: subtotal - discount + vat };
}

const CLINIC_TZ = "Asia/Bangkok";
const DAY_KEYS: readonly DayCode[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const pad2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);
const keyToUtc = (key: string) => {
  const [year, month, day] = key.split("-").map(Number);
  return Date.UTC(year as number, (month as number) - 1, day as number);
};
const utcKey = (ms: number) => {
  const date = new Date(ms);
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`;
};
const addKeyDays = (key: string, n: number) =>
  utcKey(keyToUtc(key) + n * 86_400_000);
const weekdayIndex = (key: string) => new Date(keyToUtc(key)).getUTCDay();
const todayKey = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: CLINIC_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const scopeOf = (value: string | undefined | null) =>
  value && value !== "all" ? value : null;
const isDateKey = (value: unknown): value is string =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
const isValidDateKey = (value: unknown) => {
  if (!isDateKey(value)) return false;
  const year = +value.slice(0, 4);
  const month = +value.slice(5, 7);
  const day = +value.slice(8, 10);
  const probe = new Date(Date.UTC(year, month - 1, day));
  return (
    probe.getUTCFullYear() === year &&
    probe.getUTCMonth() === month - 1 &&
    probe.getUTCDate() === day
  );
};
const hasDatePart = (value: unknown) => {
  if (typeof value !== "string") return false;
  const text: string = value;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return isValidDateKey(text);
  return /^\d{4}-\d{2}-\d{2}T/.test(text) && isValidDateKey(text.slice(0, 10));
};
const isTimeOfDay = (value: unknown) =>
  typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
const minutesOf = (value: string) =>
  +value.slice(0, 2) * 60 + +value.slice(3, 5);

/** Bangkok calendar day key for any instant/date value. */
export function businessDay(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (isDateKey(value)) return isValidDateKey(value) ? value : null;
  const date = new Date(value as string | number);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: CLINIC_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function periodKeys(
  period: "today" | "week" | "month",
  anchor?: string | null,
): string[] {
  const today = businessDay(anchor) ?? todayKey();
  if (!today) return [];
  if (period === "week") {
    const offset = (weekdayIndex(today) + 6) % 7;
    const monday = addKeyDays(today, -offset);
    return Array.from({ length: 7 }, (_, index) => addKeyDays(monday, index));
  }
  if (period === "month") {
    const year = +today.slice(0, 4);
    const month = +today.slice(5, 7);
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return Array.from({ length: days }, (_, index) =>
      utcKey(Date.UTC(year, month - 1, index + 1)),
    );
  }
  return [today];
}

/** Rolling day keys (oldest first) used by the prototype's `dayList`. */
export function recentDayKeys(days: number): string[] {
  const today = todayKey();
  return Array.from({ length: days }, (_, index) =>
    addKeyDays(today, index - (days - 1)),
  );
}

const INACTIVE_DOC = new Set(["draft", "cancelled"]);
const ARRIVAL = new Set(["arrived", "checkedIn", "waiting", "withDoctor", "completed"]);

const ITEM_DEFS: ReadonlyArray<readonly [string, string, string, string, ItemGroup, string, number, number]> = [
  ["IT-01", "MED-BTX-100", "โบทูลินัม ท็อกซิน 100 ยูนิต", "Botulinum toxin 100 U", "MED", "vial", 8500, 6],
  ["IT-02", "MED-HA-001", "ฟิลเลอร์กรดไฮยาลูโรนิก 1 มล.", "Hyaluronic acid filler 1 ml", "MED", "syringe", 4200, 8],
  ["IT-03", "MED-LIDO-2", "ลิโดเคน 2% 20 มล.", "Lidocaine 2% 20 ml", "MED", "vial", 180, 10],
  ["IT-04", "MED-MNX-5", "มินอกซิดิล 5% 60 มล.", "Minoxidil 5% solution 60 ml", "MED", "bottle", 690, 12],
  ["IT-05", "MED-ADP-01", "อะดาพาลีน 0.1% เจล 15 ก.", "Adapalene 0.1% gel 15 g", "MED", "tube", 320, 10],
  ["IT-06", "MED-TRT-05", "เทรติโนอิน 0.05% ครีม", "Tretinoin 0.05% cream", "MED", "tube", 260, 10],
  ["IT-07", "CON-LSR-TIP", "หัวเลเซอร์ (ใช้ครั้งเดียว)", "Laser handpiece tip (single use)", "CON", "pcs", 950, 15],
  ["IT-08", "CON-VTC-20", "เซรั่มวิตามินซี 20%", "Vitamin C 20% serum", "CON", "ampoule", 240, 20],
  ["IT-09", "CON-PEEL-30", "สารผลัดผิว AHA 30%", "Chemical peel solution AHA 30%", "CON", "bottle", 540, 6],
  ["IT-10", "CON-SYR-3", "กระบอกฉีดยา 3 มล.", "Disposable syringe 3 ml", "CON", "pcs", 8, 100],
  ["IT-11", "CON-MN-36", "หัวไมโครนีดเดิล 36 เข็ม", "Microneedle cartridge 36-pin", "CON", "pcs", 420, 12],
  ["IT-12", "SUP-GLV-M", "ถุงมือไนไตรล์ M (กล่อง)", "Nitrile gloves M (box)", "SUP", "box", 290, 8],
  ["IT-13", "SUP-GZE-44", "ผ้าก๊อซ 4x4 นิ้ว (แพ็ค)", "Gauze 4x4 in (pack)", "SUP", "pack", 65, 30],
  ["IT-14", "SUP-ALC-70", "แอลกอฮอล์ 70% 500 มล.", "Alcohol 70% 500 ml", "SUP", "bottle", 75, 24],
  ["IT-15", "MED-BTX-050", "โบทูลินัม ท็อกซิน 50 ยูนิต", "Botulinum toxin 50 U", "MED", "vial", 4600, 4],
  ["IT-16", "MED-VTB-01", "วิตามินบีคอมเพล็กซ์ (หลอด)", "Vitamin B complex ampoule", "MED", "ampoule", 95, 30],
  ["IT-17", "MED-HYD-15", "ไฮยาลูโรนิเดส 1500 IU", "Hyaluronidase 1500 IU", "MED", "vial", 2100, 3],
  ["IT-18", "RTL-SUN-50", "กันแดด SPF50 50 มล. (ขายปลีก)", "Sunscreen SPF50 50 ml (retail)", "RTL", "bottle", 890, 12],
  ["IT-19", "RTL-CLN-150", "โฟมล้างหน้า 150 มล. (ขายปลีก)", "Cleanser 150 ml (retail)", "RTL", "bottle", 550, 12],
  ["IT-20", "CON-PRP-KIT", "ชุดเก็บ PRP", "PRP preparation kit", "CON", "kit", 1600, 5],
  ["IT-21", "CON-PDO-01", "ไหมเงี่ยง PDO (ชุด)", "PDO thread lift (set)", "CON", "set", 3200, 4],
  ["IT-22", "SUP-MSK-01", "หน้ากากอนามัย (กล่อง)", "Face mask (box)", "SUP", "box", 120, 10],
  ["IT-23", "SRV-CONS-01", "ค่าปรึกษาแพทย์", "Consultation service", "SRV", "visit", 800, 0],
  ["IT-24", "SRV-LSR-01", "เลเซอร์กระตุ้นรากผม", "Hair laser session", "SRV", "session", 4500, 0],
] as const;

export function buildOps(core: CoreData = coreData): OpsData {
  const dates = clinicDates(new Date(core.now));
  const rand = seededRng(SEED_OPS);
  const int = (min: number, max: number) =>
    Math.floor(rand() * (max - min + 1)) + min;
  const pick = <T>(rows: readonly T[]): T =>
    rows[Math.floor(rand() * rows.length) % rows.length] as T;

  const warehouses: Warehouse[] = [
    { id: "WH-MAIN", docType: "Warehouse", name: { th: "คลังยาหลัก", en: "Main pharmacy store" }, sample: true },
    { id: "WH-ROOM", docType: "Warehouse", name: { th: "คลังห้องหัตถการ", en: "Procedure room store" }, sample: true },
    { id: "WH-FRONT", docType: "Warehouse", name: { th: "คลังหน้าร้าน", en: "Front retail shelf" }, sample: true },
  ];

  const items: Item[] = ITEM_DEFS.map((def) => ({
    id: def[0],
    docType: "Item",
    code: def[1],
    name: { th: def[2], en: def[3] },
    group: def[4],
    uom: def[5],
    price: def[6],
    reorder: def[7],
    isStock: def[4] !== "SRV",
    batchTracked: def[4] !== "SRV" && def[4] !== "RTL",
    sample: true,
  }));

  const itemById = (id: string) => items.find((item) => item.id === id) ?? null;

  // ---------------------------------------------------------------- stock
  // The prototype generated opening movements and then replaced them with a
  // reconciled ledger. The RNG draws behind those opening rows still shape the
  // generated lots, so the generation stays faithful; only the reconciled
  // ledger is exported (each lot sums to its balance).
  const batches: Batch[] = [];
  let batchSeq = 2400;
  for (const item of items) {
    if (!item.isStock) continue;
    const count = item.group === "MED" ? int(1, 3) : int(1, 2);
    for (let b = 0; b < count; b++) {
      batchSeq++;
      const expiryOffset = pick([-14, 12, 22, 45, 80, 140, 200, 320, 420, 520]);
      const batchWarehouse = rand() > 0.7 ? "WH-ROOM" : "WH-MAIN";
      const received = int(24, 180);
      const moved = rand() > 0.45 ? Math.min(int(4, 26), Math.floor(received / 2)) : 0;
      const used = moved ? Math.min(int(1, 9), moved) : 0;
      const batch: Batch = {
        id: `B-${batchSeq}`,
        docType: "Batch",
        batchId: `LOT-${2410 + batchSeq - 2400}-${item.code.slice(-3)}`,
        itemId: item.id,
        warehouse: batchWarehouse,
        expiry: dates.at(expiryOffset, "12:00"),
        qty: received - moved - used,
        cost: Math.round(item.price * 0.62),
        sample: true,
      };

      // Superseded opening rows: kept only so the seeded draws match.
      dates.at(-int(18, 26), "10:20");
      int(11, 28);
      if (moved) {
        dates.at(-int(8, 16), "14:05");
        int(31, 58);
        if (used) int(101, 128);
      }
      batches.push(batch);
    }
  }

  const reconciled: Movement[] = [];
  batches.forEach((batch, index) => {
    const used = 2 + (index % 5);
    reconciled.push({
      id: `SAMPLE-SLE-${index}-R`,
      docType: "Stock Ledger Entry",
      at: dates.at(-25, "10:00"),
      itemId: batch.itemId,
      batchId: batch.id,
      warehouse: batch.warehouse,
      qty: batch.qty + used,
      type: "receipt",
      ref: `SAMPLE-PR-${String(index + 1).padStart(3, "0")}`,
      sample: true,
    });
    reconciled.push({
      id: `SAMPLE-SLE-${index}-U`,
      docType: "Stock Ledger Entry",
      at: dates.at(-1, "15:00"),
      itemId: batch.itemId,
      batchId: batch.id,
      warehouse: batch.warehouse,
      qty: -used,
      type: "usage",
      ref: `SAMPLE-PROC-${String(index + 1).padStart(3, "0")}`,
      sample: true,
    });
  });
  // Fix: chronological order (the prototype sorted newest-first while its
  // ledger header implied an immutable ledger read).
  const movements = reconciled.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));

  // Explicit sample reorder thresholds per item/location. A location without
  // a record is unavailable (null), never an implicit zero. Retail stock has
  // no configured point on purpose; IT-01/IT-07/IT-12 are deliberately tight
  // so the low-stock KPI has sample rows.
  const totalsByKey = new Map<string, number>();
  for (const batch of batches) {
    const key = `${batch.itemId}|${batch.warehouse}`;
    totalsByKey.set(key, (totalsByKey.get(key) ?? 0) + batch.qty);
  }
  const tight: Record<string, number> = { "IT-01": 1, "IT-07": 1, "IT-12": 1 };
  const stockThresholds: StockThreshold[] = [];
  for (const [key, total] of totalsByKey) {
    const [itemId, warehouse] = key.split("|") as [string, string];
    const item = itemById(itemId);
    if (!item || !item.isStock || item.group === "RTL") continue;
    const share = warehouse === "WH-ROOM" ? 0.4 : 0.6;
    const level = tight[itemId]
      ? Math.ceil(total * 1.25)
      : Math.max(1, Math.round(item.reorder * share));
    stockThresholds.push({
      id: `SAMPLE-REORDER-${itemId}-${warehouse}`,
      docType: "Item Reorder",
      itemId,
      warehouse,
      reorderLevel: level,
      source: "sample",
      sample: true,
    });
  }

  // ------------------------------------------------------------ suppliers
  const suppliers: Supplier[] = [
    { id: "SUP-01", docType: "Supplier", name: { th: "บางกอก เมดิคอล ซัพพลาย", en: "Bangkok Medical Supply" }, phone: "02-441-8899", contact: "คุณสมหมาย", terms: "30 วัน", sample: true },
    { id: "SUP-02", docType: "Supplier", name: { th: "ไทย โบเทค ดิสทริบิวชัน", en: "Thai Biotech Distribution" }, phone: "02-772-1144", contact: "คุณวิภา", terms: "45 วัน", sample: true },
    { id: "SUP-03", docType: "Supplier", name: { th: "เอสเธติก ซัพพลาย เฮาส์", en: "Aesthetic Supply House" }, phone: "02-259-3300", contact: "คุณธนกฤต", terms: "15 วัน", sample: true },
    { id: "SUP-04", docType: "Supplier", name: { th: "ฟาร์มา พลัส (ประเทศไทย)", en: "Pharma Plus (Thailand)" }, phone: "02-884-6622", contact: "คุณอารีย์", terms: "30 วัน", sample: true },
  ];
  const supplierById = (id: string) =>
    suppliers.find((supplier) => supplier.id === id) ?? (suppliers[0] as Supplier);

  // ----------------------------------------------------------- purchasing
  const purchaseOrders: PurchaseOrder[] = [
    { id: "DC-PO-0108", docType: "Purchase Order", supplierId: "SUP-02", warehouse: "WH-MAIN", date: dates.at(-12, "11:00"), required: dates.at(-2, "11:00"), status: "partlyReceived",
      items: [{ itemId: "IT-01", qty: 20, received: 12, rate: 5900, warehouse: "WH-MAIN" }, { itemId: "IT-15", qty: 10, received: 10, rate: 3200, warehouse: "WH-MAIN" }], sample: true },
    { id: "DC-PO-0109", docType: "Purchase Order", supplierId: "SUP-01", warehouse: "WH-MAIN", date: dates.at(-9, "09:30"), required: dates.at(-1, "09:30"), status: "submitted",
      items: [{ itemId: "IT-10", qty: 500, received: 0, rate: 6, warehouse: "WH-MAIN" }, { itemId: "IT-12", qty: 24, received: 0, rate: 210, warehouse: "WH-MAIN" }], sample: true },
    { id: "DC-PO-0110", docType: "Purchase Order", supplierId: "SUP-03", warehouse: "WH-ROOM", date: dates.at(-5, "14:20"), required: dates.at(3, "14:20"), status: "submitted",
      items: [{ itemId: "IT-07", qty: 30, received: 0, rate: 610, warehouse: "WH-ROOM" }, { itemId: "IT-11", qty: 24, received: 0, rate: 280, warehouse: "WH-ROOM" }], sample: true },
    { id: "DC-PO-0111", docType: "Purchase Order", supplierId: "SUP-04", warehouse: "WH-MAIN", date: dates.at(-2, "10:15"), required: dates.at(6, "10:15"), status: "draft",
      items: [{ itemId: "IT-04", qty: 40, received: 0, rate: 470, warehouse: "WH-MAIN" }, { itemId: "IT-05", qty: 30, received: 0, rate: 210, warehouse: "WH-MAIN" }], sample: true },
    { id: "DC-PO-0107", docType: "Purchase Order", supplierId: "SUP-01", warehouse: "WH-MAIN", date: dates.at(-24, "13:00"), required: dates.at(-14, "13:00"), status: "closed",
      items: [{ itemId: "IT-13", qty: 60, received: 60, rate: 45, warehouse: "WH-MAIN" }, { itemId: "IT-14", qty: 40, received: 40, rate: 52, warehouse: "WH-MAIN" }], sample: true },
    // Fix: DC-PR-0019 used to reference this missing PO. The closed sample PO
    // now exists, so the receipt resolves to a real lot/line instead of
    // dangling with an unmapped item.
    { id: "DC-PO-0106", docType: "Purchase Order", supplierId: "SUP-01", warehouse: "WH-MAIN", date: dates.at(-20, "09:00"), required: dates.at(-10, "09:00"), status: "closed",
      items: [{ itemId: "IT-12", qty: 24, received: 24, rate: 290, warehouse: "WH-MAIN" }], sample: true },
  ];

  const receipts: PurchaseReceipt[] = [
    { id: "DC-PR-0021", docType: "Purchase Receipt", poId: "DC-PO-0108", supplierId: "SUP-02", date: dates.at(-2, "11:20"), status: "submitted", value: 12 * 5900 + 10 * 3200, items: 2, by: "thanapon",
      lines: [{ itemId: "IT-01", qty: 12, value: 12 * 5900 }, { itemId: "IT-15", qty: 10, value: 10 * 3200 }], sample: true },
    { id: "DC-PR-0020", docType: "Purchase Receipt", poId: "DC-PO-0107", supplierId: "SUP-01", date: dates.at(-14, "10:05"), status: "submitted", value: 60 * 45 + 40 * 52, items: 2, by: "thanapon",
      lines: [{ itemId: "IT-13", qty: 60, value: 60 * 45 }, { itemId: "IT-14", qty: 40, value: 40 * 52 }], sample: true },
    { id: "DC-PR-0019", docType: "Purchase Receipt", poId: "DC-PO-0106", supplierId: "SUP-01", date: dates.at(-19, "15:40"), status: "submitted", value: 24 * 290, items: 1, by: "thanapon",
      lines: [{ itemId: "IT-12", lineIndex: 0, qty: 24, value: 24 * 290 }], sample: true },
  ];

  const receiptLines = (
    receipt: PurchaseReceipt | undefined,
    po?: PurchaseOrder,
  ) => {
    if (!receipt) return [];
    if (Array.isArray(receipt.lines)) {
      return receipt.lines
        .filter((line) => line && typeof line.qty === "number" && line.qty > 0)
        .map((line) => {
          const claim =
            po && typeof line.lineIndex === "number" && po.items[line.lineIndex]
              ? line.lineIndex
              : null;
          const lineItem = claim !== null ? po?.items[claim]?.itemId ?? null : null;
          if (claim !== null && line.itemId && line.itemId !== lineItem) {
            return {
              lineIndex: null,
              claimedLineIndex: claim,
              itemId: line.itemId,
              qty: line.qty,
              conflict: true,
            };
          }
          return {
            lineIndex: claim,
            itemId: line.itemId ?? lineItem ?? null,
            qty: line.qty,
            conflict: false,
          };
        });
    }
    return [];
  };

  const poProgress = (poId: string) => {
    const po = purchaseOrders.find((order) => order.id === poId);
    if (!po) return null;
    const items = po.items;
    const seen = new Set<string>();
    const receiptIds: string[] = [];
    const unmatchedLines: Array<{
      receiptId: string;
      lineIndex: number | null;
      itemId: string | null;
      qty: number;
      reason: string;
    }> = [];
    const lineReceived = items.map(() => 0);

    for (const receipt of receipts) {
      if (receipt.status !== "submitted" || receipt.poId !== poId || seen.has(receipt.id)) {
        continue;
      }
      seen.add(receipt.id);
      receiptIds.push(receipt.id);
      for (const line of receiptLines(receipt, po)) {
        if (line.conflict) {
          unmatchedLines.push({ receiptId: receipt.id, lineIndex: line.claimedLineIndex ?? null, itemId: line.itemId, qty: line.qty, reason: "item-mismatch" });
          continue;
        }
        if (line.lineIndex !== null) {
          lineReceived[line.lineIndex] = (lineReceived[line.lineIndex] ?? 0) + line.qty;
          continue;
        }
        if (!line.itemId) {
          unmatchedLines.push({ receiptId: receipt.id, lineIndex: null, itemId: null, qty: line.qty, reason: "unmapped" });
          continue;
        }
        const matches = items
          .map((item, index) => (item.itemId === line.itemId ? index : -1))
          .filter((index) => index !== -1);
        if (matches.length === 1) {
          const index = matches[0] as number;
          lineReceived[index] = (lineReceived[index] ?? 0) + line.qty;
          continue;
        }
        unmatchedLines.push({ receiptId: receipt.id, lineIndex: null, itemId: line.itemId, qty: line.qty, reason: matches.length ? "ambiguous-item" : "item-not-on-po" });
      }
    }

    const lines = items.map((line, index) => {
      const received = lineReceived[index] ?? 0;
      return {
        lineIndex: index,
        poLineId: `${poId}:${index}`,
        itemId: line.itemId,
        ordered: line.qty,
        received,
        pending: Math.max(0, line.qty - received),
        overReceived: Math.max(0, received - line.qty),
      };
    });
    const ordered = lines.reduce((sum, line) => sum + line.ordered, 0);
    const received = lines.reduce((sum, line) => sum + line.received, 0);
    return {
      ordered,
      received,
      pending: lines.reduce((sum, line) => sum + line.pending, 0),
      overReceived: lines.reduce((sum, line) => sum + line.overReceived, 0),
      percent: ordered > 0 ? Math.min(100, Math.round((received / ordered) * 100)) : 0,
      lines,
      receiptIds: receiptIds.sort(),
      unmatchedLines,
    };
  };

  // -------------------------------------------------------------- billing
  const invoices: Invoice[] = [];
  const payments: Payment[] = [];
  const completedVisits: Visit[] = core.visits.filter(
    (visit) => visit.state === "completed",
  );

  const invoiceForVisit = (visit: Visit, index: number) => {
    const appointment = visit.appointmentId
      ? core.appointmentById(visit.appointmentId)
      : null;
    const priceBase = appointment
      ? core.appointmentTypeById(appointment.typeId).price
      : 800;
    const lines: InvoiceLine[] = [
      {
        itemId: appointment
          ? appointment.typeId === "AT-03"
            ? "IT-24"
            : "IT-23"
          : "IT-23",
        desc: null,
        qty: 1,
        rate: priceBase,
      },
    ];
    if (rand() > 0.55) {
      const retail = items.filter((item) => item.group === "RTL");
      const item = retail[int(0, 1)] as Item;
      lines.push({ itemId: item.id, desc: null, qty: 1, rate: item.price });
    }
    const { subtotal, discount, vat, total } = invoiceTotals(
      lines,
      rand() > 0.82 ? 0.1 : 0,
    );
    const paidRoll = rand();
    const paid =
      paidRoll > 0.24
        ? total
        : paidRoll > 0.12
          ? Math.round(total * 0.5)
          : 0;
    const invoice: Invoice = {
      id: `DC-INV-${String(210 + index).padStart(5, "0")}`,
      docType: "Sales Invoice",
      docstatus: 1,
      patientId: visit.patientId,
      visitId: visit.id,
      date: visit.date,
      lines,
      subtotal,
      discount,
      vat,
      total,
      paid,
      status: paid >= total ? "paid" : paid > 0 ? "partial" : "unpaid",
      dueDate: dates.addDays(visit.date, 7),
      companyId: COMPANY_ID,
      sample: true,
    };
    if (paid > 0) {
      const paymentId = `DC-PAY-${String(310 + index).padStart(4, "0")}`;
      payments.push({
        id: paymentId,
        docType: "Payment Entry",
        invoiceId: invoice.id,
        patientId: visit.patientId,
        date: invoice.date,
        mode: rand() > 0.55 ? "cash" : "card",
        amount: paid,
        receivedBy: "preeya",
        reference: `PAY-${index + 1}`,
        posted: true,
        companyId: COMPANY_ID,
        status: "posted",
        sample: true,
      });
      invoices.push({ ...invoice, paymentId });
    } else {
      invoices.push(invoice);
    }
  };

  completedVisits.forEach((visit, index) => invoiceForVisit(visit, index));

  const invoiceById = (id: string) =>
    invoices.find((invoice) => invoice.id === id) ?? null;
  const paymentById = (id: string) =>
    payments.find((payment) => payment.id === id) ?? null;
  const paymentsForInvoice = (invoiceId: string) =>
    payments.filter((payment) => payment.invoiceId === invoiceId);
  const invoicesFor = (patientId: string) =>
    invoices.filter((invoice) => invoice.patientId === patientId);
  const invoiceForVisitId = (visitId: string) =>
    invoices.find((invoice) => invoice.visitId === visitId) ?? null;

  // ------------------------------------------------------- staff & masters
  const staff: StaffMember[] = [
    { id: "araya", docType: "User", name: { th: "อารยา ตั้งใจ", en: "Araya Tangjai" }, role: "REC", frappeRoles: ["Receptionist"], user: "araya.t@demo-clinic.example", lang: "th", active: true, practitioner: null, lastLogin: dates.at(0, "08:42"), sample: true },
    { id: "kitti", docType: "User", name: { th: "นพ. กิตติ วรรณชัย", en: "Dr. Kitti Wannachai" }, role: "DOC", frappeRoles: ["Doctor", "Healthcare Practitioner"], user: "kitti.w@demo-clinic.example", lang: "th", active: true, practitioner: "HP-01", lastLogin: dates.at(0, "08:55"), sample: true },
    { id: "lalita", docType: "User", name: { th: "พญ. ลลิตา ปิ่นทอง", en: "Dr. Lalita Pintong" }, role: "DOC", frappeRoles: ["Doctor", "Healthcare Practitioner"], user: "lalita.p@demo-clinic.example", lang: "en", active: true, practitioner: "HP-02", lastLogin: dates.at(-1, "17:30"), sample: true },
    { id: "maneerat", docType: "User", name: { th: "มณีรัตน์ ศรีสุข", en: "Maneerat Srisuk" }, role: "NUR", frappeRoles: ["Nurse"], user: "maneerat.s@demo-clinic.example", lang: "th", active: true, practitioner: null, lastLogin: dates.at(0, "08:30"), sample: true },
    { id: "thanapon", docType: "User", name: { th: "ธนพล อินทร์ทอง", en: "Thanapon Inthong" }, role: "PHA", frappeRoles: ["Pharmacy", "Stock Manager"], user: "thanapon.i@demo-clinic.example", lang: "th", active: true, practitioner: null, lastLogin: dates.at(0, "08:05"), sample: true },
    { id: "preeya", docType: "User", name: { th: "ปรียา สุขสวัสดิ์", en: "Preeya Suksawat" }, role: "CAS", frappeRoles: ["Cashier", "Accounts User"], user: "preeya.s@demo-clinic.example", lang: "th", active: true, practitioner: null, lastLogin: dates.at(0, "08:50"), sample: true },
    { id: "surachai", docType: "User", name: { th: "สุรชัย วัฒนกิจ", en: "Surachai Wattanakit" }, role: "MGR", frappeRoles: ["Manager", "Accounts Manager"], user: "surachai.w@demo-clinic.example", lang: "en", active: true, practitioner: null, lastLogin: dates.at(-1, "18:10"), sample: true },
    { id: "naphatsorn", docType: "User", name: { th: "นภัสสร จันทร์เพ็ญ", en: "Naphatsorn Chanpen" }, role: "ADM", frappeRoles: ["System Manager"], user: "naphatsorn.c@demo-clinic.example", lang: "th", active: true, practitioner: null, lastLogin: dates.at(-1, "16:20"), sample: true },
    { id: "somchai", docType: "User", name: { th: "สมชาย วงศ์ทอง", en: "Somchai Wongthong" }, role: "REC", frappeRoles: ["Receptionist"], user: "somchai.w@demo-clinic.example", lang: "th", active: false, practitioner: null, lastLogin: dates.at(-22, "12:00"), sample: true },
  ];

  const scheduleRow = (
    practitioner: string,
    day: DayCode,
    start: string,
    end: string,
    room: string,
  ): Schedule => ({
    practitioner,
    docType: "Practitioner Schedule (sample)",
    day,
    weekday: DAY_KEYS.indexOf(day) + 1,
    dayLabelKey: "appointment.day",
    start,
    end,
    room,
    sample: true,
  });

  const schedules: Schedule[] = [
    scheduleRow("HP-01", "mon", "09:00", "17:00", "SU-01"),
    scheduleRow("HP-01", "wed", "09:00", "17:00", "SU-01"),
    scheduleRow("HP-01", "fri", "10:00", "18:00", "SU-01"),
    scheduleRow("HP-02", "tue", "09:00", "17:00", "SU-03"),
    scheduleRow("HP-02", "thu", "09:00", "17:00", "SU-03"),
    scheduleRow("HP-02", "sat", "09:00", "13:00", "SU-03"),
    scheduleRow("HP-03", "mon", "12:00", "20:00", "SU-04"),
    scheduleRow("HP-03", "thu", "12:00", "20:00", "SU-04"),
  ];

  const procedureTemplates: ProcedureTemplate[] = [
    { id: "PRT-01", docType: "Clinical Procedure Template", name: { th: "เลเซอร์กระตุ้นรากผม", en: "Hair laser session" }, minutes: 45, price: 4500, consumables: ["IT-07"], room: "SU-03", sample: true },
    { id: "PRT-02", docType: "Clinical Procedure Template", name: { th: "ฉีดโบทูลินัม ท็อกซิน", en: "Botulinum toxin injection" }, minutes: 30, price: 8500, consumables: ["IT-01", "IT-10"], room: "SU-04", sample: true },
    { id: "PRT-03", docType: "Clinical Procedure Template", name: { th: "ฉีดฟิลเลอร์", en: "Dermal filler injection" }, minutes: 40, price: 12000, consumables: ["IT-02", "IT-03"], room: "SU-04", sample: true },
    { id: "PRT-04", docType: "Clinical Procedure Template", name: { th: "ผลัดผิวด้วย AHA", en: "AHA chemical peel" }, minutes: 35, price: 2500, consumables: ["IT-09"], room: "SU-03", sample: true },
    { id: "PRT-05", docType: "Clinical Procedure Template", name: { th: "ไมโครนีดเดิล", en: "Microneedling" }, minutes: 50, price: 5500, consumables: ["IT-11", "IT-03"], room: "SU-04", sample: true },
  ];

  const labTemplates: LabTemplate[] = [
    { id: "LBT-01", docType: "Lab Test Template", name: { th: "ความสมบูรณ์ของเม็ดเลือด (CBC)", en: "Complete blood count (CBC)" }, sampleKind: { th: "เลือด", en: "Blood" }, sample: true },
    { id: "LBT-02", docType: "Lab Test Template", name: { th: "เฟอร์ริติน", en: "Ferritin" }, sampleKind: { th: "เลือด", en: "Blood" }, sample: true },
    { id: "LBT-03", docType: "Lab Test Template", name: { th: "ไทรอยด์ (TSH)", en: "Thyroid (TSH)" }, sampleKind: { th: "เลือด", en: "Blood" }, sample: true },
    { id: "LBT-04", docType: "Lab Test Template", name: { th: "น้ำตาลในเลือด (FBS)", en: "Fasting blood sugar" }, sampleKind: { th: "เลือด", en: "Blood" }, sample: true },
    { id: "LBT-05", docType: "Lab Test Template", name: { th: "ตรวจการตั้งครรภ์", en: "Pregnancy test" }, sampleKind: { th: "ปัสสาวะ", en: "Urine" }, sample: true },
  ];

  const labProviders: LabProvider[] = [
    { id: "LP-01", docType: "Lab Provider (sample extension)", name: { th: "แบงคอก เซ็นทรัล แล็บ", en: "Bangkok Central Lab" }, active: true, sample: true },
    { id: "LP-02", docType: "Lab Provider (sample extension)", name: { th: "ศูนย์แล็บรามาธิบดี (ภายนอก)", en: "Ramathibodi reference lab" }, active: true, sample: true },
    { id: "LP-03", docType: "Lab Provider (sample extension)", name: { th: "แล็บพันธมิตรสีลม", en: "Silom partner lab" }, active: false, sample: true },
  ];

  const externalLabs: ExternalLab[] = [
    { id: "DC-LAB-77", docType: "External Lab Request (sample extension)", patientId: "P-10241", visitId: "DC-VIS-2041", practitioner: "HP-01", test: { th: "CBC, เฟอร์ริติน, TSH", en: "CBC, ferritin, TSH" }, provider: "Bangkok Central Lab", status: "received", orderedAt: dates.at(0, "09:40"), sentAt: dates.at(0, "10:05"), sentBy: "maneerat", receivedAt: dates.at(0, "11:05"), receivedBy: "maneerat", reviewedAt: null, reviewedBy: null, file: "result-77.pdf", sampleKind: "Blood 4 ml", externalRef: "BCL-88412", sample: true },
    { id: "DC-LAB-76", docType: "External Lab Request (sample extension)", patientId: "P-10244", visitId: null, practitioner: "HP-03", test: { th: "ไทรอยด์ (TSH)", en: "Thyroid (TSH)" }, provider: "Ramathibodi reference lab", status: "sent", orderedAt: dates.at(-1, "14:20"), sentAt: dates.at(-1, "16:00"), sentBy: "araya", receivedAt: null, receivedBy: null, reviewedAt: null, reviewedBy: null, file: null, sampleKind: "Blood 3 ml", externalRef: "RAM-2291", sample: true },
    { id: "DC-LAB-75", docType: "External Lab Request (sample extension)", patientId: "P-10246", visitId: null, practitioner: "HP-01", test: { th: "น้ำตาลในเลือด (FBS)", en: "Fasting blood sugar" }, provider: "Bangkok Central Lab", status: "ordered", orderedAt: dates.at(0, "12:10"), sentAt: null, sentBy: null, receivedAt: null, receivedBy: null, reviewedAt: null, reviewedBy: null, file: null, sampleKind: "Blood 3 ml", externalRef: null, sample: true },
    { id: "DC-LAB-74", docType: "External Lab Request (sample extension)", patientId: "P-10248", visitId: null, practitioner: "HP-02", test: { th: "CBC", en: "CBC" }, provider: "Bangkok Central Lab", status: "reviewed", orderedAt: dates.at(-4, "10:00"), sentAt: dates.at(-4, "11:30"), sentBy: "maneerat", receivedAt: dates.at(-2, "09:40"), receivedBy: "maneerat", reviewedAt: dates.at(-1, "15:20"), reviewedBy: "HP-02", file: "result-74.pdf", sampleKind: "Blood 4 ml", externalRef: "BCL-88201", interpretation: "ค่าอยู่ในเกณฑ์ปกติ ไม่พบภาวะโลหิตจาง", sample: true },
  ];

  // Audit rows must describe a record that actually exists in this fixture set.
  // The first posted seeded payment is DC-PAY-0312 (card, ฿7,276.00) — the
  // prototype's DC-PAY-0310 cash row did not exist.
  const firstPostedPayment = payments.find((payment) => payment.posted);
  if (!firstPostedPayment) {
    throw new Error("seeded fixture expects at least one posted payment");
  }
  const firstPaymentAmount = firstPostedPayment.amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const firstPaymentMode =
    firstPostedPayment.mode === "card"
      ? { th: "บัตรเครดิต", en: "Card" }
      : { th: "เงินสด", en: "Cash" };

  const audit: AuditEntry[] = [
    { id: "AUD-1", docType: "Activity Log", at: dates.at(0, "09:52"), actor: "kitti", action: { th: "บันทึกฉบับร่างสมุดตรวจ", en: "Saved clinical note draft" }, entity: "DC-VIS-2041", note: { th: "ร่างกาย: หัวข้อประวัติ อาการสำคัญ การตรวจ", en: "Sections: history, exam" }, sample: true },
    { id: "AUD-2", docType: "Activity Log", at: dates.at(0, "11:05"), actor: "maneerat", action: { th: "อัปโหลดผลแล็บภายนอก", en: "Uploaded external lab result" }, entity: "DC-LAB-77", note: { th: "ผลไฟล์ result-77.pdf", en: "File result-77.pdf" }, sample: true },
    { id: "AUD-3", docType: "Activity Log", at: dates.at(0, "09:12"), actor: "araya", action: { th: "รับผู้ป่วยเข้าตรวจ", en: "Checked patient in" }, entity: "DC-APT-3301", note: { th: "สถานะ: อยู่กับแพทย์", en: "Status: with doctor" }, sample: true },
    { id: "AUD-4", docType: "Activity Log", at: dates.at(0, "08:55"), actor: "araya", action: { th: "เชื่อมภาพถ่ายก่อนตรวจกับนัด", en: "Linked intake photos to appointment" }, entity: "PH-2203", note: { th: "เชื่อมภายหลัง check-in", en: "Linked after check-in" }, sample: true },
    { id: "AUD-5", docType: "Activity Log", at: dates.at(-1, "15:20"), actor: "kitti", action: { th: "ตรวจทานผลแล็บ", en: "Reviewed lab result" }, entity: "DC-LAB-74", note: { th: "แพทย์ผู้สั่งตรวจรับทราบ", en: "Ordering doctor acknowledged" }, sample: true },
    { id: "AUD-6", docType: "Activity Log", at: firstPostedPayment.date, actor: "preeya", action: { th: "บันทึกการรับชำระเงิน", en: "Posted payment" }, entity: firstPostedPayment.id, note: { th: `${firstPaymentMode.th} · ฿${firstPaymentAmount}`, en: `${firstPaymentMode.en} · ฿${firstPaymentAmount}` }, sample: true },
    { id: "AUD-7", docType: "Activity Log", at: dates.at(-2, "11:20"), actor: "thanapon", action: { th: "รับสินค้าบางส่วน", en: "Received partial purchase order" }, entity: "DC-PR-0021", note: { th: "PO คงเหลือ 8 ขวด", en: "8 vials still on order" }, sample: true },
  ];

  const systemChecks: SystemCheck[] = [
    { id: "CHK-01", docType: "System Check (sample)", name: { th: "เวอร์ชัน Frappe / Marley / ERPNext", en: "Frappe / Marley / ERPNext versions" }, status: "unverified", checkedAt: null, note: { th: "ต้องรัน bench version บนไซต์จริง", en: "Requires bench version on the installed site" }, sample: true },
    { id: "CHK-02", docType: "System Check (sample)", name: { th: "ฟิลด์ Patient และ Patient Appointment", en: "Patient and appointment field map" }, status: "unverified", checkedAt: null, note: { th: "ยังไม่ตรวจ Custom Field / Property Setter", en: "Custom fields and property setters not inspected yet" }, sample: true },
    { id: "CHK-03", docType: "System Check (sample)", name: { th: "วิธี check-in ของ Marley", en: "Marley check-in method" }, status: "unverified", checkedAt: null, note: { th: "ต้นแบบนี้จำลอง ไม่ได้เรียกเมธอดจริง", en: "Simulated here; no real method is called" }, sample: true },
    { id: "CHK-04", docType: "System Check (sample)", name: { th: "งานภาพถ่ายและคำอธิบาย (Clinic Photo)", en: "Photo + annotation extension model" }, status: "extension", checkedAt: null, note: { th: "ต้องสร้าง DocType ใหม่", en: "New DocType required" }, sample: true },
    { id: "CHK-05", docType: "System Check (sample)", name: { th: "แล็บภายนอก (External Lab)", en: "External lab workflow" }, status: "extension", checkedAt: null, note: { th: "ยังไม่มีโมเดลผู้ให้บริการภายนอก", en: "No outside-provider model today" }, sample: true },
    { id: "CHK-06", docType: "System Check (sample)", name: { th: "Batch / Serial and Batch Bundle", en: "Batch / Serial and Batch Bundle" }, status: "unverified", checkedAt: null, note: { th: "ต้องทดสอบการรับเข้าและการตัดสต็อก", en: "Receipt and consumption must be tested" }, sample: true },
    { id: "CHK-07", docType: "System Check (sample)", name: { th: "Payment Entry และบัญชีเงินสด/บัตร", en: "Payment Entry & cash/card accounts" }, status: "unverified", checkedAt: null, note: { th: "ยังไม่ได้ตั้งค่าบัญชีในไซต์จริง", en: "Accounts not confirmed on site" }, sample: true },
    { id: "CHK-08", docType: "System Check (sample)", name: { th: "ที่เก็บภาษาและสิทธิ์ผู้ใช้", en: "User language & permission model" }, status: "decision", checkedAt: null, note: { th: "ต้องตัดสินใจว่าจะซิงก์ User.language หรือไม่", en: "Decide whether to sync with User.language" }, sample: true },
  ];

  const company: Company = {
    id: COMPANY_ID,
    docType: "Company",
    name: { th: "เดโม คลินิก (ดีซี)", en: "Demo Clinic (DC)" },
    currency: "THB",
    country: "Thailand",
    sample: true,
  };

  // ----------------------------------------------------------- read helpers
  const stockOnHand = (itemId: string, warehouse?: string) =>
    batches.reduce((sum, batch) => {
      if (batch.itemId !== itemId) return sum;
      if (warehouse && batch.warehouse !== warehouse) return sum;
      return sum + batch.qty;
    }, 0);

  const stockStatus = (itemId: string): "out" | "low" | "ok" => {
    const onHand = stockOnHand(itemId);
    const item = itemById(itemId);
    if (onHand <= 0) return "out";
    if (item && onHand <= item.reorder) return "low";
    return "ok";
  };

  const daysToExpiry = (iso: string) =>
    Math.round((new Date(iso).getTime() - Date.now()) / 86_400_000);

  const batchStatus = (batch: Batch): "expired" | "expiring" | "ok" => {
    const days = daysToExpiry(batch.expiry);
    if (days < 0) return "expired";
    if (days <= 30) return "expiring";
    return "ok";
  };

  const resolveConsumableBatch = (itemId: string, warehouse: string) => {
    const usable = batches
      .filter(
        (batch) =>
          batch.itemId === itemId &&
          batch.warehouse === warehouse &&
          batch.qty > 0 &&
          daysToExpiry(batch.expiry) >= 0,
      )
      .sort((a, b) => (a.expiry < b.expiry ? -1 : 1));
    return usable[0] ?? null;
  };

  // ------------------------------------------------------------ v3 helpers
  const expiryDays = (expiry: string, asOf?: string) => {
    const e = businessDay(expiry);
    const a = asOf ? businessDay(asOf) : todayKey();
    if (!e || !a) return null;
    return Math.round((keyToUtc(e) - keyToUtc(a)) / 86_400_000);
  };

  const urgencyOf = (days: number | null) => {
    if (days === null) return "later";
    if (days < 0) return "expired";
    if (days <= 7) return "7";
    if (days <= 30) return "30";
    if (days <= 90) return "90";
    return "later";
  };

  const expiryRows = (warehouse?: string, window = "all") => {
    const wh = scopeOf(warehouse);
    const today = todayKey();
    let rows = batches
      .filter((batch) => batch.qty > 0 && (!wh || batch.warehouse === wh))
      .map((batch) => {
        const days = expiryDays(batch.expiry, today);
        return {
          id: batch.id,
          batchId: batch.batchId,
          itemId: batch.itemId,
          warehouse: batch.warehouse,
          expiry: batch.expiry,
          onHand: batch.qty,
          cost: batch.cost,
          daysRemaining: days,
          usableQty: days === null ? 0 : days < 0 ? 0 : batch.qty,
          urgency: urgencyOf(days),
        };
      });
    if (window === "expired") {
      rows = rows.filter((row) => row.daysRemaining !== null && row.daysRemaining < 0);
    } else if (window !== "all") {
      const limit = +window;
      rows = rows.filter(
        (row) =>
          row.daysRemaining !== null &&
          row.daysRemaining >= 0 &&
          row.daysRemaining <= limit,
      );
    }
    return rows.sort((a, b) => {
      if (a.daysRemaining === null) return 1;
      if (b.daysRemaining === null) return -1;
      return a.daysRemaining - b.daysRemaining;
    });
  };

  const thresholdFor = (itemId: string, warehouse: string) =>
    stockThresholds.find(
      (threshold) =>
        threshold.itemId === itemId && threshold.warehouse === warehouse,
    )?.reorderLevel ?? null;

  const stockRows = (warehouse?: string) => {
    const scope = scopeOf(warehouse);
    const today = todayKey();
    const pairs = new Set<string>();
    for (const batch of batches) {
      if (scope && batch.warehouse !== scope) continue;
      pairs.add(`${batch.itemId}|${batch.warehouse}`);
    }
    for (const threshold of stockThresholds) {
      if (scope && threshold.warehouse !== scope) continue;
      pairs.add(`${threshold.itemId}|${threshold.warehouse}`);
    }
    const rows = [];
    for (const key of pairs) {
      const [itemId, wh] = key.split("|") as [string, string];
      const item = itemById(itemId);
      if (!item || !item.isStock) continue;
      const lotRows = batches.filter(
        (batch) => batch.itemId === itemId && batch.warehouse === wh,
      );
      const onHand = lotRows.reduce((sum, batch) => sum + batch.qty, 0);
      const usable = lotRows.reduce((sum, batch) => {
        const days = expiryDays(batch.expiry, today);
        return sum + (days === null || days < 0 ? 0 : batch.qty);
      }, 0);
      const level = thresholdFor(itemId, wh);
      rows.push({
        itemId,
        warehouse: wh,
        onHand,
        usable,
        reorderLevel: level,
        low: level !== null && usable > 0 && usable <= level,
        out: usable <= 0,
        needed: level === null ? null : Math.max(0, level - usable),
      });
    }
    return rows.sort((a, b) =>
      a.itemId === b.itemId
        ? a.warehouse < b.warehouse
          ? -1
          : 1
        : a.itemId < b.itemId
          ? -1
          : 1,
    );
  };

  const eligibleInvoice = (invoice: Invoice | null) =>
    !!invoice && !INACTIVE_DOC.has(invoice.status);

  const paymentRows = (filters?: {
    period?: "today" | "week" | "month" | "all";
    anchor?: string;
    mode?: string;
    company?: string;
  }) => {
    const config = filters ?? {};
    const keys =
      config.period && config.period !== "all"
        ? periodKeys(config.period, config.anchor)
        : null;
    const rows = [];
    for (const payment of payments) {
      if (!payment.posted || INACTIVE_DOC.has(payment.status ?? "posted")) continue;
      if (scopeOf(config.mode) && payment.mode !== config.mode) continue;
      const invoice = invoiceById(payment.invoiceId);
      if (!eligibleInvoice(invoice)) continue;
      const companyId = invoice?.companyId ?? payment.companyId;
      if (scopeOf(config.company) && companyId !== config.company) continue;
      if (keys) {
        const day = businessDay(payment.date);
        if (!day || !keys.includes(day)) continue;
      }
      rows.push({
        id: payment.id,
        invoiceId: invoice?.id ?? payment.invoiceId,
        patientId: payment.patientId,
        date: payment.date,
        mode: payment.mode,
        amount: payment.amount,
        posted: payment.posted,
        reference: payment.reference,
        receivedBy: payment.receivedBy,
        invoiceDate: invoice?.date ?? null,
        company: companyId,
      });
    }
    return rows.sort((a, b) => {
      const ta = new Date(a.date).getTime();
      const tb = new Date(b.date).getTime();
      if (ta === tb || Number.isNaN(ta) || Number.isNaN(tb)) {
        return a.id < b.id ? 1 : -1;
      }
      return tb - ta;
    });
  };

  const dueOf = (invoice: Invoice) => {
    const allocated = payments
      .filter(
        (payment) =>
          payment.posted &&
          payment.invoiceId === invoice.id &&
          !INACTIVE_DOC.has(payment.status ?? "posted"),
      )
      .reduce((sum, payment) => sum + payment.amount, 0);
    return Math.max(0, invoice.total - allocated);
  };

  const outstandingRows = () =>
    invoices
      .filter(eligibleInvoice)
      .map((invoice) => ({ ...invoice, due: dueOf(invoice) }))
      .filter((row) => row.due > 0);

  const appointmentsByStatus = (dayKeyValue?: string) => {
    const key = dayKeyValue ?? todayKey();
    const tally: Record<string, number> = {};
    for (const appointment of core.appointments) {
      if (appointment.date.slice(0, 10) !== key) continue;
      tally[appointment.status] = (tally[appointment.status] ?? 0) + 1;
    }
    return tally;
  };

  const doctorWorkload = (days = 7) => {
    const keys = recentDayKeys(days);
    return core.practitioners.map((practitioner) => ({
      practitioner: practitioner.id,
      count: core.appointments.filter(
        (appointment) =>
          appointment.practitioner === practitioner.id &&
          keys.includes(businessDay(appointment.date) ?? ""),
      ).length,
    }));
  };

  const revenueByDay = (days = 7) =>
    recentDayKeys(days).map((key) => {
      const dayInvoices = invoices.filter(
        (invoice) => invoice.date.slice(0, 10) === key,
      );
      const dayPayments = payments.filter(
        (payment) => payment.date.slice(0, 10) === key,
      );
      return {
        key,
        invoiced: dayInvoices.reduce((sum, invoice) => sum + invoice.total, 0),
        collected: dayPayments.reduce((sum, payment) => sum + payment.amount, 0),
        invoiceCount: dayInvoices.length,
      };
    });

  const totals = (days = 7) => {
    const keys = recentDayKeys(days);
    const periodInvoices = invoices.filter((invoice) =>
      keys.includes(invoice.date.slice(0, 10)),
    );
    const periodPayments = payments.filter((payment) =>
      keys.includes(payment.date.slice(0, 10)),
    );
    const periodAppointments = core.appointments.filter((appointment) =>
      keys.includes(appointment.date.slice(0, 10)),
    );
    const invoiced = periodInvoices.reduce((sum, invoice) => sum + invoice.total, 0);
    const collected = periodPayments.reduce((sum, payment) => sum + payment.amount, 0);
    const expiring = batches.filter((batch) => {
      const days = Math.round(
        (new Date(batch.expiry).getTime() - Date.now()) / 86_400_000,
      );
      return batch.qty > 0 && days >= 0 && days <= 30;
    });
    const expired = batches.filter(
      (batch) => batch.qty > 0 && new Date(batch.expiry).getTime() < Date.now(),
    );
    return {
      invoiced,
      collected,
      outstanding: invoiced - collected,
      invoiceCount: periodInvoices.length,
      appointments: periodAppointments.length,
      seen: periodAppointments.filter(
        (appointment) => appointment.status === "completed",
      ).length,
      cancelled: periodAppointments.filter(
        (appointment) =>
          appointment.status === "cancelled" || appointment.status === "noShow",
      ).length,
      procedures: core.visits.filter(
        (visit) =>
          keys.includes(visit.date.slice(0, 10)) &&
          visit.procedures &&
          visit.procedures.length > 0,
      ).length,
      inventoryValue: batches.reduce(
        (sum, batch) => sum + batch.qty * batch.cost,
        0,
      ),
      expiringCount: expiring.length,
      expiredCount: expired.length,
      lowStock: items.filter((item) => item.isStock && stockStatus(item.id) === "low")
        .length,
      outOfStock: items.filter((item) => item.isStock && stockStatus(item.id) === "out")
        .length,
    };
  };

  const topServices = (days = 7) => {
    const keys = recentDayKeys(days);
    const tally = new Map<string, { itemId: string; qty: number; amount: number }>();
    for (const invoice of invoices) {
      if (!keys.includes(invoice.date.slice(0, 10))) continue;
      for (const line of invoice.lines) {
        const row = tally.get(line.itemId) ?? {
          itemId: line.itemId,
          qty: 0,
          amount: 0,
        };
        row.qty += line.qty;
        row.amount += line.qty * line.rate;
        tally.set(line.itemId, row);
      }
    }
    return [...tally.values()]
      .sort((a, b) => b.amount - a.amount)
      .slice(0, 5);
  };

  // Deep copies: KPI callers must never hold a live reference into the ledger
  // (the prototype deep-cloned rows for exactly this reason).
  const metricInvoiceRows = (keys: string[]) =>
    invoices
      .filter(
        (invoice) =>
          eligibleInvoice(invoice) &&
          keys.includes(businessDay(invoice.date) ?? ""),
      )
      .map((invoice) => JSON.parse(JSON.stringify(invoice)) as Invoice);

  const metrics = (
    period: "today" | "week" | "month",
    options?: { anchor?: string; warehouse?: string; practitioner?: string },
  ) => {
    const config = options ?? {};
    const anchor = config.anchor ?? todayKey();
    const warehouse = scopeOf(config.warehouse);
    const practitioner = config.practitioner ?? null;
    const keys = periodKeys(period, anchor);
    const appointmentRows = core.appointments.filter(
      (appointment) =>
        keys.includes(businessDay(appointment.date) ?? "") &&
        (!practitioner || appointment.practitioner === practitioner),
    );
    const checkedIn = appointmentRows.filter(
      (appointment) => appointment.checkedInAt || ARRIVAL.has(appointment.status),
    );
    const completed = core.visits.filter(
      (visit) =>
        visit.state === "completed" &&
        (!practitioner || visit.practitioner === practitioner) &&
        keys.includes(businessDay(visit.date) ?? ""),
    );
    const invoiceRows = metricInvoiceRows(keys);
    const payRows = paymentRows({ period, anchor });
    const dueRows = outstandingRows();
    const stock = stockRows(warehouse ?? undefined);
    const expiry = expiryRows(warehouse ?? undefined, "30");
    return {
      appointments: appointmentRows.length,
      checkedIn: checkedIn.length,
      completed: completed.length,
      invoiced: invoiceRows.reduce((sum, invoice) => sum + invoice.total, 0),
      collected: payRows.reduce((sum, payment) => sum + payment.amount, 0),
      outstanding: dueRows.reduce((sum, invoice) => sum + invoice.due, 0),
      lowStock: stock.filter((row) => row.low || row.out).length,
      expiring: expiry.length,
      appointmentRows,
      invoiceRows,
      paymentRows: payRows,
      outstandingRows: dueRows,
      stockRows: stock,
      expiryRows: expiry,
    };
  };

  // ---------------------------------------------------------- working hours
  const sampleHours = () => {
    const today = todayKey();
    const toNextMonday = (8 - weekdayIndex(today)) % 7 || 7;
    return {
      timezone: CLINIC_TZ,
      clinic: [
        { day: "mon" as DayCode, start: "09:00", end: "20:00" },
        { day: "tue" as DayCode, start: "09:00", end: "20:00" },
        { day: "wed" as DayCode, start: "09:00", end: "20:00" },
        { day: "thu" as DayCode, start: "09:00", end: "20:00" },
        { day: "fri" as DayCode, start: "09:00", end: "20:00" },
        { day: "sat" as DayCode, start: "09:00", end: "13:00" },
      ],
      breaks: [
        { practitioner: "HP-01", day: "mon" as DayCode, start: "12:00", end: "13:00" },
        { practitioner: "HP-02", day: "tue" as DayCode, start: "12:00", end: "12:30" },
        { practitioner: "HP-03", day: "thu" as DayCode, start: "16:00", end: "16:30" },
      ],
      leave: [
        {
          practitioner: "HP-01",
          date: addKeyDays(today, toNextMonday),
          start: "09:00",
          end: "17:00",
        },
      ],
    };
  };

  const validateIntervals = (
    list: ReadonlyArray<Record<string, string>>,
    groupOf: (entry: Record<string, string>) => string,
    overlapKey: string,
    requireDay: boolean,
  ): { ok: boolean; error?: string } => {
    const byGroup = new Map<string, Array<Record<string, string>>>();
    for (const entry of list) {
      if (!entry || typeof entry !== "object") return { ok: false, error: "invalid.shape" };
      if (requireDay && !DAY_KEYS.includes(entry.day as DayCode)) {
        return { ok: false, error: "invalid.day" };
      }
      if (!requireDay && entry.day !== undefined && !DAY_KEYS.includes(entry.day as DayCode)) {
        return { ok: false, error: "invalid.day" };
      }
      if (!isTimeOfDay(entry.start) || !isTimeOfDay(entry.end)) {
        return { ok: false, error: "invalid.time" };
      }
      if (minutesOf(entry.end as string) <= minutesOf(entry.start as string)) {
        return { ok: false, error: "invalid.order" };
      }
      const group = groupOf(entry);
      const rows = byGroup.get(group) ?? [];
      rows.push(entry);
      byGroup.set(group, rows);
    }
    for (const rows of byGroup.values()) {
      const sorted = [...rows].sort(
        (a, b) => minutesOf(a.start as string) - minutesOf(b.start as string),
      );
      for (let index = 1; index < sorted.length; index++) {
        const current = sorted[index] as Record<string, string>;
        const previous = sorted[index - 1] as Record<string, string>;
        if (minutesOf(current.start as string) < minutesOf(previous.end as string)) {
          return { ok: false, error: overlapKey };
        }
      }
    }
    return { ok: true };
  };

  const isKnownPractitioner = (id: string) =>
    id === "all" || core.practitioners.some((practitioner) => practitioner.id === id);

  const validateHours = (config: unknown): { ok: boolean; error?: string } => {
    if (!config || typeof config !== "object") return { ok: false, error: "invalid.shape" };
    const rows = config as ReturnType<typeof sampleHours>;
    if (rows.timezone !== CLINIC_TZ) return { ok: false, error: "invalid.timezone" };
    if (!Array.isArray(rows.clinic) || !Array.isArray(rows.breaks) || !Array.isArray(rows.leave)) {
      return { ok: false, error: "invalid.shape" };
    }
    const clinic = validateIntervals(
      rows.clinic as unknown as Array<Record<string, string>>,
      (entry) => `clinic|${entry.day}`,
      "overlap.clinic",
      true,
    );
    if (!clinic.ok) return clinic;
    const breaks = validateIntervals(
      rows.breaks as unknown as Array<Record<string, string>>,
      (entry) => `break|${entry.practitioner ?? ""}|${entry.day}`,
      "overlap.break",
      true,
    );
    if (!breaks.ok) return breaks;
    for (const row of rows.breaks) {
      if (!isKnownPractitioner(row.practitioner)) {
        return { ok: false, error: "invalid.practitioner" };
      }
    }
    for (const row of rows.leave) {
      if (!row || !isValidDateKey(row.date)) return { ok: false, error: "invalid.date" };
      if (!isKnownPractitioner(row.practitioner)) {
        return { ok: false, error: "invalid.practitioner" };
      }
    }
    return validateIntervals(
      rows.leave as unknown as Array<Record<string, string>>,
      (entry) => `leave|${entry.practitioner ?? ""}|${entry.date}`,
      "overlap.leave",
      false,
    );
  };

  const hours = () => sampleHours();

  const overlapsWindow = (
    entry: { start: string; end: string },
    start: number,
    end: number,
  ) => minutesOf(entry.start) < end && minutesOf(entry.end) > start;

  const bangkokMinutes = (value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    const hhmm = new Intl.DateTimeFormat("en-GB", {
      timeZone: CLINIC_TZ,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
    return minutesOf(hhmm);
  };

  const appointmentMinutes = (appointment: {
    date: string;
    duration: number;
  }) => {
    const start = bangkokMinutes(appointment.date);
    if (start === null) return null;
    const duration =
      typeof appointment.duration === "number" ? appointment.duration : 30;
    return { start, end: start + duration };
  };

  const availability = (options: {
    practitioner?: string;
    date?: string;
    time?: string;
    minutes?: number;
    excludeId?: string;
    room?: string;
  }) => {
    const config = hours();
    const check = validateHours(config);
    if (!check.ok) return { available: false, reason: "invalid", source: "hours" };
    const practitioner = options.practitioner;
    const day = businessDay(options.date);
    const start = isTimeOfDay(options.time) ? minutesOf(options.time as string) : null;
    const minutes = options.minutes;
    if (
      !practitioner ||
      !hasDatePart(options.date) ||
      !isDateKey(day) ||
      start === null ||
      typeof minutes !== "number" ||
      !Number.isFinite(minutes) ||
      minutes <= 0 ||
      Math.floor(minutes) !== minutes
    ) {
      return { available: false, reason: "invalid", source: "input" };
    }
    const end = start + minutes;
    const weekday = DAY_KEYS[(weekdayIndex(day) + 6) % 7] as DayCode;
    const clinic = config.clinic.filter((row) => row.day === weekday);
    if (!clinic.length) return { available: false, reason: "closed", source: "clinic" };
    if (
      !clinic.some(
        (row) => minutesOf(row.start) <= start && minutesOf(row.end) >= end,
      )
    ) {
      return { available: false, reason: "closed", source: "clinic" };
    }
    const scheduled = schedules.filter(
      (row) => row.practitioner === practitioner && row.day === weekday,
    );
    if (
      !scheduled.some(
        (row) => minutesOf(row.start) <= start && minutesOf(row.end) >= end,
      )
    ) {
      return { available: false, reason: "schedule", source: "schedules" };
    }
    const leave = config.leave.filter(
      (row) =>
        (row.practitioner === practitioner || row.practitioner === "all") &&
        businessDay(row.date) === day,
    );
    if (leave.some((row) => overlapsWindow(row, start, end))) {
      return { available: false, reason: "leave", source: "hours" };
    }
    const breaks = config.breaks.filter(
      (row) =>
        row.day === weekday &&
        (row.practitioner === practitioner || row.practitioner === "all"),
    );
    if (breaks.some((row) => overlapsWindow(row, start, end))) {
      return { available: false, reason: "break", source: "hours" };
    }
    const sameDay = core.appointments.filter(
      (appointment) =>
        appointment.id !== options.excludeId &&
        businessDay(appointment.date) === day &&
        appointment.status !== "cancelled" &&
        appointment.status !== "noShow",
    );
    const conflict = sameDay.some((appointment) => {
      if (appointment.practitioner !== practitioner) return false;
      const span = appointmentMinutes(appointment);
      return !!span && span.start < end && span.end > start;
    });
    if (conflict) return { available: false, reason: "conflict", source: "appointments" };
    if (options.room) {
      const roomBusy = sameDay.some((appointment) => {
        if (appointment.room !== options.room) return false;
        const span = appointmentMinutes(appointment);
        return !!span && span.start < end && span.end > start;
      });
      if (roomBusy) return { available: false, reason: "room", source: "appointments" };
    }
    return { available: true, reason: null, source: "clinic+schedules" };
  };

  return {
    now: core.now,
    dates,
    warehouses,
    items,
    batches,
    movements,
    stockThresholds,
    suppliers,
    purchaseOrders,
    receipts,
    invoices,
    payments,
    staff,
    schedules,
    procedureTemplates,
    labTemplates,
    labProviders,
    externalLabs,
    audit,
    systemChecks,
    company,
    counts: {
      ...core.counts,
      bills: invoices.filter((invoice) => invoice.status !== "paid").length,
    },
    itemById,
    batchById: (id: string) => batches.find((batch) => batch.id === id) ?? null,
    invoiceById,
    paymentById,
    supplierById,
    invoiceForVisit: invoiceForVisitId,
    invoicesFor,
    paymentsForInvoice,
    stockOnHand,
    stockStatus,
    batchStatus,
    daysToExpiry,
    resolveConsumableBatch,
    poProgress,
    paymentRows,
    dueOf,
    outstandingRows,
    appointmentsByStatus,
    doctorWorkload,
    revenueByDay,
    totals,
    topServices,
    metrics,
    expiryRows,
    stockRows,
    expiryDays,
    availability,
    validateHours,
    hours,
  };
}

export const opsData: OpsData = buildOps(coreData);

export const warehouses = opsData.warehouses;
export const items = opsData.items;
export const batches = opsData.batches;
export const movements = opsData.movements;
export const stockThresholds = opsData.stockThresholds;
export const suppliers = opsData.suppliers;
export const purchaseOrders = opsData.purchaseOrders;
export const receipts = opsData.receipts;
export const invoices = opsData.invoices;
export const payments = opsData.payments;
export const staff = opsData.staff;
export const schedules = opsData.schedules;
export const procedureTemplates = opsData.procedureTemplates;
export const labTemplates = opsData.labTemplates;
export const labProviders = opsData.labProviders;
export const externalLabs = opsData.externalLabs;
export const audit = opsData.audit;
export const systemChecks = opsData.systemChecks;
export const company = opsData.company;
/** Canonical counts, including the invoice-derived `bills` (26 seeded). */
export const counts = opsData.counts;

export const itemById = opsData.itemById;
export const batchById = opsData.batchById;
export const invoiceById = opsData.invoiceById;
export const paymentById = opsData.paymentById;
export const supplierById = opsData.supplierById;
export const invoiceForVisit = opsData.invoiceForVisit;
export const invoicesFor = opsData.invoicesFor;
export const paymentsForInvoice = opsData.paymentsForInvoice;
export const stockOnHand = opsData.stockOnHand;
export const stockStatus = opsData.stockStatus;
export const batchStatus = opsData.batchStatus;
export const daysToExpiry = opsData.daysToExpiry;

// v3 helpers bound to the default fixture set (same closures as `opsData`).
export const poProgress = opsData.poProgress;
export const paymentRows = opsData.paymentRows;
export const dueOf = opsData.dueOf;
export const outstandingRows = opsData.outstandingRows;
export const appointmentsByStatus = opsData.appointmentsByStatus;
export const doctorWorkload = opsData.doctorWorkload;
export const revenueByDay = opsData.revenueByDay;
export const totals = opsData.totals;
export const topServices = opsData.topServices;
export const metrics = opsData.metrics;
export const expiryRows = opsData.expiryRows;
export const stockRows = opsData.stockRows;
export const expiryDays = opsData.expiryDays;
export const availability = opsData.availability;
export const validateHours = opsData.validateHours;
export const hours = opsData.hours;
export const resolveConsumableBatch = opsData.resolveConsumableBatch;
