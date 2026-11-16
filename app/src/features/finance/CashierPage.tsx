import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { cashierShift } from "../../fixtures/data-authored";
import { opsData } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  companyDisplayKey,
  completedVisitsAwaitingBilling,
  invoiceStatusKey,
  patientLabel,
  paymentModeKey,
  shiftTotals,
} from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const INVOICE_TONES = {
  draft: "neutral",
  unpaid: "warning",
  partial: "info",
  paid: "success",
  cancelled: "danger",
} as const;

/**
 * CAS-01 pending bills and the shift. Due balances come from the helper's
 * positive-due rows (all dates, including partial invoices); the date and mode
 * filters only narrow the posted-collection list. Shift totals are derived from
 * the posted payments dated on the shift day — zero when there are none, never
 * a fabricated figure. Company filtering is unavailable in the sample data and
 * says so instead of guessing.
 */
export function CashierPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money, date } = useOpsFormat();
  const today = opsData.dates.todayKey();
  const [collectionDate, setCollectionDate] = useState(today);
  const [mode, setMode] = useState<"all" | "cash" | "card">("all");

  const totals = shiftTotals();
  const dueRows = useMemo(() => opsData.outstandingRows(), []);
  const dueTotal = dueRows.reduce((sum, row) => sum + row.due, 0);

  const dayRows = useMemo(
    () =>
      opsData
        .paymentRows({})
        .filter(
          (row) => opsData.dates.dayKey(row.date) === collectionDate,
        ),
    [collectionDate],
  );
  const collections =
    mode === "all" ? dayRows : dayRows.filter((row) => row.mode === mode);
  const cash = dayRows
    .filter((row) => row.mode === "cash")
    .reduce((sum, row) => sum + row.amount, 0);
  const card = dayRows
    .filter((row) => row.mode === "card")
    .reduce((sum, row) => sum + row.amount, 0);
  const awaiting = completedVisitsAwaitingBilling();
  const cashier = opsData.staff.find((row) => row.id === cashierShift.cashier);
  const companyKey = companyDisplayKey(cashierShift.companyId);

  return (
    <div data-surface="CAS-01" data-testid="cashier-page">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>CAS-01</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("รายการรอเรียกเก็บ", "Pending bills")}
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

        <Card
          title={copy("รายการรอเรียกเก็บ", "Pending bills")}
          hint={copy(
            "ยอดค้างรวมทุกวันตามบริษัทที่เลือก รวมใบแจ้งหนี้ชำระบางส่วน วันและวิธีชำระใช้กับรายการรับเงินเท่านั้น บัตรยอดเงินสด/บัตรแสดงแต่ละวิธีในวันที่เลือก",
            "Due balances cover all dates for selected company, including partial invoices. Date/mode filter the collection list only. Cash/card cards each show their own mode on selected date.",
          )}
        >
          <dl className="pc-dl">
            <div>
              <dt>{copy("พนักงาน", "Staff")}</dt>
              <dd>{cashier ? cashier.name[locale] : cashierShift.cashier}</dd>
            </div>
            <div>
              <dt>{copy("บริษัท", "Company")}</dt>
              <dd>{companyKey ? copy("เดโม คลินิก", "Demo Clinic") : "—"}</dd>
            </div>
            <div>
              <dt>{copy("เงินสด/บัตร", "Cash/card accounts")}</dt>
              <dd className="ops-mono">
                {cashierShift.cashAccount} · {cashierShift.cardAccount}
              </dd>
            </div>
            <div>
              <dt>{copy("เงินสด", "Cash")}</dt>
              <dd>{money(totals.cash)}</dd>
            </div>
            <div>
              <dt>{copy("บัตรเครดิต", "Credit card")}</dt>
              <dd>{money(totals.card)}</dd>
            </div>
            <div>
              <dt>{copy("ยอดรวม", "Total")}</dt>
              <dd>
                <strong>{money(totals.total)}</strong> ·{" "}
                {totals.paymentCount}{" "}
                {copy("รายการรับเงิน", "Payment")}
              </dd>
            </div>
          </dl>
          <p className="pc-note">
            {copy(
              "รับเงินสดที่โพสต์และจัดสรร",
              "Posted allocated cash",
            )}
            : {money(totals.cash)} · {copy("รับบัตรที่โพสต์และจัดสรร", "Posted allocated card")}: {money(totals.card)}
          </p>
        </Card>

        <FilterBar>
          <FilterField label={copy("วันที่ตามปฏิทิน", "Calendar date")}>
            <input
              type="date"
              value={collectionDate}
              onChange={(event) => setCollectionDate(event.target.value)}
            />
          </FilterField>
          <FilterField label={copy("วิธีชำระ", "Payment mode")}>
            <select
              value={mode}
              onChange={(event) =>
                setMode(event.target.value as "all" | "cash" | "card")
              }
            >
              <option value="all">{copy("ทั้งหมด", "All")}</option>
              <option value="cash">{copy("เงินสด", "Cash")}</option>
              <option value="card">{copy("บัตรเครดิต", "Credit card")}</option>
            </select>
          </FilterField>
          <FilterField label={copy("บริษัท", "Company")}>
            <select
              value=""
              disabled
              aria-describedby="company-source-unavailable"
            >
              <option value="">
                {copy(
                  "ไม่มีแหล่งบริษัทในข้อมูลตัวอย่าง",
                  "Company source unavailable in sample",
                )}
              </option>
            </select>
          </FilterField>
        </FilterBar>
        <p className="pc-field__hint" id="company-source-unavailable">
          {copy(
            "ไม่มีแหล่งบริษัทในข้อมูลตัวอย่าง",
            "Company source unavailable in sample",
          )}
        </p>

        <section className="pc-kpis" aria-label={copy("รายการรอเรียกเก็บ", "Pending bills")}>
          {[
            {
              key: "dueCount",
              label: copy("ใบแจ้งหนี้ค้าง · ทุกวัน", "Unpaid invoices · all dates"),
              value: String(dueRows.length),
              href: `${ROUTES.invoices}?filter=outstanding`,
            },
            {
              key: "dueTotal",
              label: copy("ยอดค้าง · ทุกวัน", "Outstanding · all dates"),
              value: money(dueTotal),
              href: `${ROUTES.invoices}?filter=outstanding`,
            },
            {
              key: "cash",
              label: copy(
                "รับเงินสดที่โพสต์และจัดสรร",
                "Posted allocated cash",
              ),
              value: money(cash),
              href: `${ROUTES.payments}?mode=cash`,
            },
            {
              key: "card",
              label: copy(
                "รับบัตรที่โพสต์และจัดสรร",
                "Posted allocated card",
              ),
              value: money(card),
              href: `${ROUTES.payments}?mode=card`,
            },
          ].map((cardRow) => (
            <Link className="pc-kpi" to={cardRow.href} key={cardRow.key}>
              <span className="pc-kpi__label">{cardRow.label}</span>
              <strong className="pc-kpi__value">{cardRow.value}</strong>
            </Link>
          ))}
        </section>

        <Card
          title={copy("คิวค้างชำระ", "Outstanding queue")}
          hint={`${copy("วันที่ตามปฏิทิน", "Calendar date")}: ${date(
            `${today}T00:00:00+07:00`,
          )}`}
          flush
        >
          <DataTable
            caption={copy("คิวค้างชำระ", "Outstanding queue")}
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
              { key: "status", label: copy("สถานะ", "Status") },
              { key: "action", label: "" },
            ]}
            rows={dueRows.slice(0, 20).map((invoice) => [
              <span key="invoice">
                {invoice.id}
                <br />
                <span className="pc-note">
                  {patientLabel(invoice.patientId, locale)}
                </span>
              </span>,
              money(invoice.total),
              money(invoice.due),
              <Badge key="status" tone={INVOICE_TONES[invoice.status]}>
                {t(invoiceStatusKey(invoice.status))}
              </Badge>,
              <Link
                key="action"
                className="pc-button pc-button--secondary pc-button--sm"
                to={`${ROUTES.invoices}?id=${encodeURIComponent(invoice.id)}`}
              >
                {copy("เปิดรายการ", "Open invoice")}
              </Link>,
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <Card
          title={copy(
            "ตรวจเสร็จ รอออกใบแจ้งหนี้",
            "Completed visits awaiting billing",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "ตรวจเสร็จ รอออกใบแจ้งหนี้",
              "Completed visits awaiting billing",
            )}
            columns={[
              { key: "visit", label: copy("การตรวจ", "Visit") },
              { key: "patient", label: copy("ผู้ป่วย", "Patient") },
              {
                key: "handoff",
                label: copy("สถานะส่งต่องาน", "Handoff state"),
              },
              { key: "action", label: "" },
            ]}
            rows={awaiting.map((row) => [
              <span key="visit">
                {row.id}
                {row.sample ? (
                  <>
                    {" "}
                    <Badge tone="demo">{copy("ข้อมูลตัวอย่าง", "Demo data")}</Badge>
                  </>
                ) : null}
              </span>,
              patientLabel(row.patientId, locale),
              copy("ต้องเรียกเก็บเงิน", "Billing needed"),
              <Link
                key="action"
                className="pc-button pc-button--secondary pc-button--sm"
                to={`${ROUTES.invoices}?visit=${encodeURIComponent(row.id)}`}
              >
                {copy("เปิดงานเรียกเก็บ", "Open billing")}
              </Link>,
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <Card
          title={copy(
            "แหล่งรับเงินตามตัวกรอง",
            "Filtered posted allocation sources",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "แหล่งรับเงินตามตัวกรอง",
              "Filtered posted allocation sources",
            )}
            columns={[
              { key: "payment", label: copy("รายการรับเงิน", "Payment") },
              { key: "mode", label: copy("วิธี", "Mode") },
              {
                key: "amount",
                label: copy("จัดสรรแล้ว", "Allocated amount"),
                align: "end",
              },
            ]}
            rows={collections.map((row) => [
              <span key="payment">
                {row.id}
                <br />
                <span className="pc-note ops-mono">{row.invoiceId}</span>
              </span>,
              t(paymentModeKey(row.mode)),
              money(row.amount),
            ])}
            empty={copy("ยังไม่มีรายการโพสต์", "No posted payments")}
          />
        </Card>
      </div>
    </div>
  );
}
