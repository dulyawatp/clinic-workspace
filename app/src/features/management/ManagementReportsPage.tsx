import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import {
  inventoryTrend,
  purchaseOrderHistory,
  reportExports,
} from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import {
  type Invoice,
  type PaymentRow,
  opsData,
  periodKeys,
} from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  itemName,
  patientLabel,
  poStatusKey,
  supplierName,
  warehouseName,
} from "../operations/ops-model";
import {
  CsvButton,
  FilterBar,
  FilterField,
  MiniChart,
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";
import { useTranslation } from "react-i18next";

type Period = "today" | "week" | "month";
const PERIOD_DAYS: Record<Period, number> = { today: 1, week: 7, month: 30 };

type ReportTab =
  | "appointments"
  | "procedures"
  | "finance"
  | "inventory"
  | "purchasing"
  | "workload"
  | "exports";

const TABS: ReadonlyArray<{ id: ReportTab; th: string; en: string }> = [
  { id: "appointments", th: "นัดหมาย", en: "Appointments" },
  { id: "procedures", th: "รายการใบแจ้งหนี้", en: "Invoice items" },
  { id: "finance", th: "การเงิน", en: "Finance" },
  { id: "inventory", th: "คลัง", en: "Inventory" },
  { id: "purchasing", th: "จัดซื้อ", en: "Purchasing" },
  { id: "workload", th: "แพทย์", en: "Practitioners" },
  {
    id: "exports",
    th: "ส่งออกรายการตามตัวกรอง",
    en: "Export filtered source records",
  },
];

/**
 * MGR-02 management reports. Every tab is a scoped source-record view of the
 * sample data for the selected calendar period; totals come from the fixture
 * helpers, never from a second calculation. The exports tab downloads the same
 * rows client-side as CSV — a local blob, no server export — and the inventory
 * and purchasing tabs add the derived transaction timelines.
 */
export function ManagementReportsPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money, date, dateTime } = useOpsFormat();
  const [params] = useSearchParams();
  const initialTab = params.get("tab");
  const initialPeriod = params.get("period");
  const metric = params.get("metric") ?? "";
  const [tab, setTab] = useState<ReportTab>(
    TABS.some((row) => row.id === initialTab)
      ? (initialTab as ReportTab)
      : "appointments",
  );
  const [period, setPeriod] = useState<Period>(
    initialPeriod === "today" || initialPeriod === "month"
      ? initialPeriod
      : "week",
  );

  const metrics = useMemo(() => opsData.metrics(period), [period]);
  const days = PERIOD_DAYS[period];
  const keys = useMemo(() => periodKeys(period), [period]);
  const inPeriod = (iso: string) => keys.includes(opsData.dates.dayKey(iso));

  const invoices = metrics.invoiceRows as Invoice[];
  const payments = metrics.paymentRows as PaymentRow[];

  // KPI drill-downs branch the cohort, as the prototype's report links do.
  const appointmentRows =
    metric === "checkedIn"
      ? metrics.appointmentRows.filter(
          (row) =>
            row.checkedInAt !== null ||
            ["arrived", "checkedIn", "waiting", "withDoctor", "completed"].includes(
              row.status,
            ),
        )
      : metrics.appointmentRows;
  const completedVisits =
    metric === "completed"
      ? coreData.visits.filter(
          (visit) => visit.state === "completed" && inPeriod(visit.date),
        )
      : [];
  const showInvoices = metric !== "collected";
  const showPayments = metric !== "invoiced" && metric !== "outstanding";
  const stock =
    metric === "lowStock"
      ? metrics.stockRows.filter((row) => row.low || row.out)
      : metrics.stockRows;
  const expiry = metrics.expiryRows;

  const invoiceItems = useMemo(() => {
    const tally = new Map<
      string,
      { itemId: string; qty: number; amount: number; ids: Set<string> }
    >();
    for (const invoice of invoices) {
      for (const line of invoice.lines) {
        const row =
          tally.get(line.itemId) ??
          { itemId: line.itemId, qty: 0, amount: 0, ids: new Set<string>() };
        row.qty += line.qty;
        row.amount += line.qty * line.rate;
        row.ids.add(invoice.id);
        tally.set(line.itemId, row);
      }
    }
    return [...tally.values()].sort((a, b) => b.amount - a.amount);
  }, [invoices]);

  const workload = useMemo(() => opsData.doctorWorkload(days), [days]);
  const trend = useMemo(() => inventoryTrend(days), [days]);
  const history = useMemo(() => purchaseOrderHistory(), []);
  const receipts = opsData.receipts.filter((receipt) => inPeriod(receipt.date));
  const movements = opsData.movements.filter((movement) => inPeriod(movement.at));

  const csvByTab: Record<ReportTab, ReadonlyArray<ReadonlyArray<string | number>>> = {
    appointments: [
      ["SAMPLE", "Appointment", "Date", "Practitioner", "Status"],
      ...metrics.appointmentRows.map((row) => [
        "SAMPLE",
        row.id,
        row.date,
        row.practitioner,
        row.status,
      ]),
    ],
    procedures: [
      ["SAMPLE", "Item", "Quantity", "Line value", "Source invoices"],
      ...invoiceItems.map((row) => [
        "SAMPLE",
        row.itemId,
        row.qty,
        row.amount,
        [...row.ids].join(" "),
      ]),
    ],
    finance: [
      ["SAMPLE", "Source", "ID", "Date", "Amount"],
      ...invoices.map((invoice) => [
        "SAMPLE",
        "invoice",
        invoice.id,
        invoice.date,
        invoice.total,
      ]),
      ...payments.map((payment) => [
        "SAMPLE",
        "payment",
        payment.id,
        payment.date,
        payment.amount,
      ]),
    ],
    inventory: [
      ["SAMPLE", "Item", "Warehouse", "Physical", "Usable", "Threshold"],
      ...stock.map((row) => [
        "SAMPLE",
        row.itemId,
        row.warehouse,
        row.onHand,
        row.usable,
        row.reorderLevel ?? "Unavailable",
      ]),
    ],
    purchasing: [
      [
        "SAMPLE",
        "PO",
        "Supplier",
        "Required date",
        "Ordered",
        "Received",
        "Pending",
        "Submitted receipt sources",
      ],
      ...history.map((row) => [
        "SAMPLE",
        row.poId,
        row.supplierId,
        opsData.purchaseOrders.find((po) => po.id === row.poId)?.required ?? "",
        row.ordered,
        row.received,
        row.pending,
        row.receiptIds.join(" "),
      ]),
    ],
    workload: [
      ["SAMPLE", "Practitioner", "Appointments"],
      ...workload.map((row) => ["SAMPLE", row.practitioner, row.count]),
    ],
    exports: [
      ["SAMPLE", "Report", "Format"],
      ...reportExports.map((row) => ["SAMPLE", row.id, row.format]),
    ],
  };

  return (
    <div data-surface="MGR-02" data-testid="management-reports">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>MGR-02</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("รายงานบริหาร", "Management reports")}
      />
      <div className="pc-page__body">
        <SampleBanner
          label={copy("ข้อมูลตัวอย่าง", "Demo data")}
          title={copy(
            "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
            "Browser-local sample records · server contracts unverified",
          )}
        />
        <SourceNote />
        <FilterBar>
          <FilterField label={copy("ช่วงเวลา", "Period")}>
            <select
              value={period}
              onChange={(event) => setPeriod(event.target.value as Period)}
            >
              <option value="today">{copy("วันนี้", "Today")}</option>
              <option value="week">{copy("สัปดาห์นี้", "This week")}</option>
              <option value="month">{copy("เดือนนี้", "This month")}</option>
            </select>
          </FilterField>
        </FilterBar>
        <OpsTabs
          label={copy("รายงานบริหาร", "Management reports")}
          active={tab}
          onChange={(next) => setTab(next as ReportTab)}
          tabs={TABS.map((row) => ({ id: row.id, label: copy(row.th, row.en) }))}
        />

        {tab === "appointments" ? (
          metric === "completed" ? (
            <Card title={copy("การตรวจ", "Visit")} flush>
              <DataTable
                caption={copy("การตรวจ", "Visit")}
                columns={[
                  { key: "visit", label: copy("การตรวจ", "Visit") },
                  { key: "patient", label: copy("ผู้ป่วย", "Patient") },
                  {
                    key: "completed",
                    label: copy("วันจบการตรวจ", "Completed at"),
                  },
                ]}
                rows={completedVisits.map((visit) => [
                  visit.id,
                  patientLabel(visit.patientId, locale),
                  dateTime(visit.date),
                ])}
                empty={copy(
                  "ไม่พบรายการตามตัวกรอง",
                  "No records match these filters",
                )}
              />
            </Card>
          ) : (
            <Card title={copy("ระเบียนต้นทางตามขอบเขต", "Scoped source records")} flush>
              <DataTable
                caption={copy("นัดหมาย", "Appointments")}
                columns={[
                  {
                    key: "appointment",
                    label: copy("นัดหมาย / ผู้ป่วย", "Appointment / patient"),
                  },
                  { key: "date", label: copy("วันนัด", "Business date") },
                  { key: "practitioner", label: copy("แพทย์", "Practitioner") },
                  { key: "status", label: copy("สถานะ", "Status") },
                ]}
                rows={appointmentRows.map((row) => [
                  <span key="appointment">
                    {row.id} · {patientLabel(row.patientId, locale)}
                  </span>,
                  dateTime(row.date),
                  row.practitioner,
                  t(`status.${row.status}`),
                ])}
                empty={copy(
                  "ไม่พบรายการตามตัวกรอง",
                  "No records match these filters",
                )}
              />
            </Card>
          )
        ) : null}

        {tab === "procedures" ? (
          <Card title={copy("รายการในใบแจ้งหนี้", "Invoice item")} flush>
            <DataTable
              caption={copy("รายการในใบแจ้งหนี้", "Invoice item")}
              columns={[
                { key: "item", label: copy("รายการในใบแจ้งหนี้", "Invoice item") },
                { key: "qty", label: copy("จำนวน", "Quantity"), align: "end" },
                {
                  key: "amount",
                  label: copy("มูลค่ารายการก่อนภาษี", "Line value before tax"),
                  align: "end",
                },
                { key: "sources", label: copy("ใบแจ้งหนี้ต้นทาง", "Source invoices") },
              ]}
              rows={invoiceItems.map((row) => [
                itemName(row.itemId, locale),
                row.qty,
                money(row.amount),
                <span key="sources" className="ops-mono">
                  {[...row.ids].join(", ")}
                </span>,
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          </Card>
        ) : null}

        {tab === "finance" ? (
          <>
            {showInvoices ? (
              metric === "outstanding" ? (
                <Card
                  title={copy("ค้างชำระ · ทุกวัน", "Outstanding · all dates")}
                  flush
                >
                  <DataTable
                    caption={copy("ค้างชำระ · ทุกวัน", "Outstanding · all dates")}
                    columns={[
                      {
                        key: "invoice",
                        label: copy("ใบแจ้งหนี้ / ผู้ป่วย", "Invoice / patient"),
                      },
                      {
                        key: "total",
                        label: copy("ยอดรวม", "Total"),
                        align: "end",
                      },
                      {
                        key: "due",
                        label: copy("ค้างชำระ", "Outstanding"),
                        align: "end",
                      },
                    ]}
                    rows={metrics.outstandingRows.map((invoice) => [
                      <span key="invoice">
                        {invoice.id}
                        <br />
                        <span className="pc-note">
                          {patientLabel(invoice.patientId, locale)}
                        </span>
                      </span>,
                      money(invoice.total),
                      money(invoice.due),
                    ])}
                    empty={copy(
                      "ไม่พบรายการตามตัวกรอง",
                      "No records match these filters",
                    )}
                  />
                </Card>
              ) : (
                <Card title={copy("ใบแจ้งหนี้ในช่วง", "Period invoices")} flush>
                  <DataTable
                    caption={copy("ใบแจ้งหนี้ในช่วง", "Period invoices")}
                    columns={[
                      {
                        key: "invoice",
                        label: copy("ใบแจ้งหนี้ / ผู้ป่วย", "Invoice / patient"),
                      },
                      {
                        key: "total",
                        label: copy("ยอดรวม", "Total"),
                        align: "end",
                      },
                      {
                        key: "paid",
                        label: copy("ชำระแล้ว", "Paid"),
                        align: "end",
                      },
                    ]}
                    rows={invoices.map((invoice) => [
                      <span key="invoice">
                        {invoice.id}
                        <br />
                        <span className="pc-note">
                          {patientLabel(invoice.patientId, locale)}
                        </span>
                      </span>,
                      money(invoice.total),
                      money(invoice.paid),
                    ])}
                    empty={copy(
                      "ไม่พบรายการตามตัวกรอง",
                      "No records match these filters",
                    )}
                  />
                </Card>
              )
            ) : null}
            {showPayments ? (
              <Card title={copy("รับเงินโพสต์และจัดสรรในช่วง", "Period posted allocated payments")} flush>
                <DataTable
                  caption={copy("รับเงินโพสต์และจัดสรรในช่วง", "Period posted allocated payments")}
                  columns={[
                    { key: "payment", label: copy("รายการรับเงิน", "Payment") },
                    { key: "date", label: copy("วันที่ตามปฏิทิน", "Calendar date") },
                    { key: "amount", label: copy("จัดสรรแล้ว", "Allocated amount"), align: "end" },
                  ]}
                  rows={payments.map((row) => [
                    row.id,
                    date(row.date),
                    money(row.amount),
                  ])}
                  empty={copy("ยังไม่มีรายการโพสต์", "No posted payments")}
                />
              </Card>
            ) : null}
          </>
        ) : null}

        {tab === "inventory" ? (
          <>
            {metric !== "expiring" ? (
            <Card title={copy("สินค้าและจุดสั่งซื้อตามคลัง", "Item–location stock & thresholds")} flush>
              <DataTable
                caption={copy("สินค้าและจุดสั่งซื้อตามคลัง", "Item–location stock & thresholds")}
                columns={[
                  { key: "item", label: copy("รายการสินค้า", "Item") },
                  { key: "warehouse", label: copy("คลัง", "Warehouse") },
                  {
                    key: "balance",
                    label: copy("คงเหลือจริง / ใช้ได้", "Physical / usable"),
                    align: "end",
                  },
                  {
                    key: "threshold",
                    label: copy("จุดสั่งซื้อ", "Reorder threshold"),
                    align: "end",
                  },
                ]}
                rows={stock.map((row) => [
                  itemName(row.itemId, locale),
                  warehouseName(row.warehouse, locale),
                  `${row.onHand} / ${row.usable}`,
                  row.reorderLevel ?? copy("ไม่พร้อมใช้งาน", "Unavailable"),
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </Card>
            ) : null}
            {metric !== "lowStock" ? (
            <Card title={copy("ล็อต", "Batch")} flush>
              <DataTable
                caption={copy("ล็อต", "Batch")}
                columns={[
                  { key: "batch", label: copy("ล็อต", "Batch") },
                  { key: "warehouse", label: copy("คลัง", "Warehouse") },
                  { key: "days", label: copy("วันคงเหลือ", "Days remaining"), align: "end" },
                  { key: "usable", label: copy("ใช้ได้", "Usable"), align: "end" },
                ]}
                rows={expiry.map((row) => [
                  row.batchId,
                  warehouseName(row.warehouse, locale),
                  row.daysRemaining ?? "—",
                  row.usableQty,
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </Card>
            ) : null}
            <Card title={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")}>
              <MiniChart
                title={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")}
                labels={trend.map((row) => row.key)}
                series={[
                  {
                    label: copy("คงเหลือจริง / ใช้ได้", "Physical / usable"),
                    tone: "ink",
                    values: trend.map((row) => row.closingUnits),
                  },
                  {
                    label: copy("เบิกใช้จริง", "Actual usage"),
                    tone: "cyan",
                    values: trend.map((row) => row.outboundUnits),
                  },
                ]}
              />
              <DataTable
                caption={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")}
                columns={[
                  { key: "date", label: copy("วันธุรกรรม", "Transaction date") },
                  { key: "document", label: copy("เอกสารต้นทาง", "Source document") },
                  { key: "item", label: copy("รายการ / คลัง", "Item / warehouse") },
                  { key: "qty", label: copy("จำนวนจากเอกสาร", "Document quantity"), align: "end" },
                ]}
                rows={movements.map((movement) => [
                  dateTime(movement.at),
                  movement.ref,
                  `${itemName(movement.itemId, locale)} / ${warehouseName(
                    movement.warehouse,
                    locale,
                  )}`,
                  movement.qty,
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </Card>
            <OpsNote>
              {copy(
                "ภาพรวมคลังปัจจุบัน ไม่มีประวัติคลังครบช่วงจึงไม่สร้างแนวโน้มยอดคงเหลือ",
                "Current stock snapshot. Full historical balances unavailable; no invented inventory trend.",
              )}
            </OpsNote>
          </>
        ) : null}

        {tab === "purchasing" ? (
          <>
            <Card title={copy("จัดซื้อและติดตามค้างรับ", "Purchasing & pending receipts")} flush>
              <DataTable
                caption={copy("จัดซื้อและติดตามค้างรับ", "Purchasing & pending receipts")}
                columns={[
                  { key: "po", label: copy("ใบสั่งซื้อ", "Purchase order") },
                  { key: "supplier", label: copy("ผู้จำหน่าย", "Supplier") },
                  { key: "required", label: copy("วันต้องการจาก PO", "Required date from PO") },
                  { key: "ordered", label: copy("สั่ง", "Ordered"), align: "end" },
                  { key: "received", label: copy("รับจากใบรับยืนยัน", "Submitted receipts"), align: "end" },
                  { key: "pending", label: copy("ค้างรับ", "Pending"), align: "end" },
                  { key: "status", label: copy("สถานะ", "Status") },
                ]}
                rows={history.map((row) => [
                  row.poId,
                  supplierName(row.supplierId, locale),
                  date(
                    opsData.purchaseOrders.find((po) => po.id === row.poId)
                      ?.required ?? "",
                  ),
                  row.ordered,
                  row.received,
                  row.pending,
                  <Badge key="status" tone="info">
                    {t(
                      poStatusKey(
                        row.status as
                          | "draft"
                          | "submitted"
                          | "partlyReceived"
                          | "closed"
                          | "cancelled",
                      ),
                    )}
                  </Badge>,
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </Card>
            <Card title={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")} flush>
              <DataTable
                caption={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")}
                columns={[
                  { key: "date", label: copy("วันธุรกรรม", "Transaction date") },
                  { key: "document", label: copy("เอกสารต้นทาง", "Source document") },
                  { key: "supplier", label: copy("ผู้จำหน่าย", "Supplier") },
                  { key: "qty", label: copy("จำนวนจากเอกสาร", "Document quantity"), align: "end" },
                ]}
                rows={receipts.map((receipt) => [
                  dateTime(receipt.date),
                  `${receipt.id} → ${receipt.poId}`,
                  receipt.supplierId
                    ? supplierName(receipt.supplierId, locale)
                    : "—",
                  receipt.lines.reduce((sum, line) => sum + line.qty, 0),
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </Card>
            <OpsNote>
              {copy(
                "ความคืบหน้าจากใบรับยืนยันที่ไม่ซ้ำ · วันต้องการจาก PO ไม่ใช่ ETA ผู้ขาย · ไม่มีประวัติการแก้ PO จึงไม่สร้างแนวโน้มย้อนหลัง",
                "Progress uses distinct submitted receipt sources. PO requested date is not supplier ETA. No PO revision history, so no invented historical trend.",
              )}
            </OpsNote>
          </>
        ) : null}

        {tab === "workload" ? (
          <Card title={copy("แพทย์", "Practitioner")} flush>
            <DataTable
              caption={copy("แพทย์", "Practitioner")}
              columns={[
                { key: "practitioner", label: copy("แพทย์", "Practitioner") },
                { key: "count", label: copy("นัดหมายทั้งหมดในช่วง", "All period appointments"), align: "end" },
              ]}
              rows={workload.map((row) => [
                opsData.staff.find((staff) => staff.practitioner === row.practitioner)
                  ?.name[locale] ?? row.practitioner,
                row.count,
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          </Card>
        ) : null}

        {tab === "exports" ? (
          <>
            <Card title={copy("ระเบียนต้นทางตามขอบเขต", "Scoped source records")}>
              <div className="ops-form__actions">
                {reportExports.map((row) => (
                  <CsvButton
                    key={row.id}
                    filename={`sample-${row.tab}.csv`}
                    rows={csvByTab[row.tab as ReportTab]}
                    label={row.label[locale]}
                  />
                ))}
              </div>
              <OpsNote>
                {copy(
                  "ส่งออกรายการตามตัวกรอง",
                  "Export filtered source records",
                )}
              </OpsNote>
            </Card>
            <Card title={copy("ลำดับธุรกรรมในช่วงปฏิทิน", "Transaction timeline within calendar period")}>
              <MiniChart
                title={copy(
                  "ยอดใบแจ้งหนี้และรับเงินรายวัน",
                  "Daily invoice and collection trend",
                )}
                labels={trend.map((row) => row.key)}
                series={[
                  {
                    label: copy("เข้า / ออก", "In / out"),
                    tone: "cyan",
                    values: trend.map((row) => row.inboundUnits),
                  },
                  {
                    label: copy("เบิกใช้จริง", "Actual usage"),
                    tone: "ink",
                    values: trend.map((row) => row.outboundUnits),
                  },
                ]}
              />
            </Card>
          </>
        ) : null}

        <div className="ops-form__actions">
          <CsvButton
            filename={`sample-${tab}.csv`}
            rows={csvByTab[tab]}
            label={copy("ส่งออกรายการตามตัวกรอง", "Export filtered source records")}
          />
        </div>
      </div>
    </div>
  );
}
