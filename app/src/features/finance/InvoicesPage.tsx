import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import {
  discountApprovalSample,
  discountPolicy,
} from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  allInvoices,
  invoiceById,
  invoiceOutstanding,
  invoiceStatusKey,
  itemName,
  patientLabel,
  refundRequestsFor,
} from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const TONES = {
  draft: "neutral",
  unpaid: "warning",
  partial: "info",
  paid: "success",
  cancelled: "danger",
} as const;

const FILTERS = ["all", "outstanding", "draft", "unpaid", "partial", "paid", "cancelled"] as const;
type InvoiceFilter = (typeof FILTERS)[number];

/**
 * CAS-02 invoices. The list shows every sample document, including the authored
 * draft and cancelled invoices, because a cashier has to see what must not be
 * collected. Detail resolves one invoice and its line/VAT/discount arithmetic
 * from the fixture totals; the discount and refund controls are designed
 * controls in an explicit not-connected state (no request is sent).
 */
export function InvoicesPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money, date } = useOpsFormat();
  const [params] = useSearchParams();
  const scopeId = params.get("id");
  const scopeVisit = params.get("visit");
  const initialFilter = params.get("filter");
  const [filter, setFilter] = useState<InvoiceFilter>(
    FILTERS.includes(initialFilter as InvoiceFilter)
      ? (initialFilter as InvoiceFilter)
      : "all",
  );
  const [search, setSearch] = useState("");

  const visitInvoice = scopeVisit
    ? allInvoices().find((invoice) => invoice.visitId === scopeVisit) ?? null
    : null;
  const selected = (scopeId ? invoiceById(scopeId) : null) ?? visitInvoice;

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return allInvoices().filter((invoice) => {
      if (filter === "outstanding") {
        if (invoiceOutstanding(invoice) <= 0) return false;
      } else if (filter !== "all" && invoice.status !== filter) {
        return false;
      }
      if (!needle) return true;
      return `${invoice.id} ${patientLabel(invoice.patientId, "th")} ${patientLabel(
        invoice.patientId,
        "en",
      )}`
        .toLowerCase()
        .includes(needle);
    });
  }, [filter, search]);

  return (
    <div data-surface="CAS-02" data-testid="invoices-page">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>CAS-02</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ใบแจ้งหนี้", "Invoices")}
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

        {scopeVisit && visitInvoice === null ? (
          <OpsNote>
            {copy(
              "การตรวจที่ยังไม่เรียกเก็บ",
              "Unbilled visit",
            )}
            : <span className="ops-mono">{scopeVisit}</span> ·{" "}
            {copy(
              "รายการที่เชื่อมแล้วไม่ออกใบแจ้งหนี้ซ้ำ ไม่ตัดสต็อกซ้ำจากหัตถการ",
              "Linked charges cannot be invoiced twice. No duplicate procedure stock deduction.",
            )}
          </OpsNote>
        ) : null}

        {selected === null && scopeId ? (
          <OpsNote>
            {copy(
              "ไม่พบรายการตามตัวกรอง",
              "No records match these filters",
            )}
          </OpsNote>
        ) : null}

        {selected ? (
          <Card
            title={`${selected.id} · ${patientLabel(selected.patientId, locale)}`}
            hint={copy(
              "รายการที่เชื่อมแล้วไม่ออกใบแจ้งหนี้ซ้ำ ไม่ตัดสต็อกซ้ำจากหัตถการ",
              "Linked charges cannot be invoiced twice. No duplicate procedure stock deduction.",
            )}
          >
            <p className="pc-status-line">
              {copy("ลูกค้าบัญชี", "Accounting customer")}:{" "}
              {coreData.patientById(selected.patientId)?.customer ||
                copy(
                  "ไม่มีการเชื่อมลูกค้า ห้ามยืนยัน",
                  "Missing mapping — submission blocked",
                )}{" "}
              · {copy("การตรวจ", "Visit")}: {selected.visitId ?? "—"} ·{" "}
              {date(selected.date)}
            </p>

            <DataTable
              caption={selected.id}
              columns={[
                { key: "item", label: copy("รายการ", "Item") },
                { key: "qty", label: copy("จำนวน", "Qty"), align: "end" },
                { key: "rate", label: copy("ราคา", "Rate"), align: "end" },
                {
                  key: "amount",
                  label: copy("จำนวนเงิน", "Amount"),
                  align: "end",
                },
              ]}
              rows={selected.lines.map((line, index) => [
                <span key={`item-${index}`}>
                  {line.desc ? line.desc[locale] : itemName(line.itemId, locale)}
                </span>,
                line.qty,
                money(line.rate),
                money(line.qty * line.rate),
              ])}
              empty={copy(
                "ไม่พบรายการตามตัวกรอง",
                "No records match these filters",
              )}
            />

            <dl className="pc-dl">
              <div>
                <dt>{copy("ก่อนส่วนลด", "Subtotal")}</dt>
                <dd>{money(selected.subtotal)}</dd>
              </div>
              <div>
                <dt>{copy("ส่วนลด", "Discount")}</dt>
                <dd>{money(selected.discount)}</dd>
              </div>
              <div>
                <dt>{copy("VAT 7%", "VAT 7%")}</dt>
                <dd>{money(selected.vat)}</dd>
              </div>
              <div>
                <dt>{copy("ยอดสุทธิ", "Total")}</dt>
                <dd>
                  <strong>{money(selected.total)}</strong>
                </dd>
              </div>
              <div>
                <dt>{copy("ค้างชำระ", "Outstanding")}</dt>
                <dd>{money(invoiceOutstanding(selected))}</dd>
              </div>
            </dl>

            <div className="ops-form__actions">
              <Link
                className="pc-button pc-button--primary"
                to={`${ROUTES.payments}?invoice=${encodeURIComponent(selected.id)}`}
              >
                {copy("รับชำระเงิน", "Receive payment")}
              </Link>
              <button
                type="button"
                className="pc-button pc-button--secondary"
                disabled
                aria-describedby="invoice-draft-unavailable"
              >
                {copy(
                  "สร้างร่างจากรายการที่ยังไม่เรียกเก็บ",
                  "Draft unbilled visit",
                )}
              </button>
              <button
                type="button"
                className="pc-button pc-button--secondary"
                disabled
                aria-describedby="invoice-refund-unavailable"
              >
                {copy("ขอคืนเงิน / ยกเลิก", "Request refund / void")}
              </button>
            </div>
            <p className="pc-field__hint" id="invoice-draft-unavailable">
              {t("checkout.writeUnavailable")}
            </p>
            <p className="pc-field__hint" id="invoice-refund-unavailable">
              {t("checkout.writeUnavailable")}
            </p>

            <div className="ops-form__grid">
              <FilterField label={copy("ส่วนลด", "Discount")}>
                <input
                  type="number"
                  min={0}
                  max={100}
                  defaultValue={0}
                  aria-describedby="discount-policy"
                />
              </FilterField>
              <div id="discount-policy" className="ops-note">
                <p>
                  {copy("ส่วนลด", "Discount")} ·{" "}
                  {discountPolicy.maxPercentWithoutApproval}% ·{" "}
                  {discountPolicy.approverRoles.join(", ")}
                </p>
                <p>
                  {discountApprovalSample.invoiceId} ·{" "}
                  {discountApprovalSample.requestedPercent}% ·{" "}
                  <Badge tone="warning">{t("status.pending")}</Badge>
                </p>
                <p className="pc-field__hint">
                  {t("checkout.writeUnavailable")}
                </p>
              </div>
            </div>
          </Card>
        ) : null}

        <Card title={copy("รายการใบแจ้งหนี้ตัวอย่าง", "Sample invoices")} flush>
          <div className="ops-card-tools">
            <FilterBar>
              <FilterField label={copy("กรองรายการ", "Filter records")}>
                <select
                  value={filter}
                  onChange={(event) =>
                    setFilter(event.target.value as InvoiceFilter)
                  }
                >
                  <option value="all">{copy("ทั้งหมด", "All")}</option>
                  <option value="outstanding">
                    {copy("ค้างชำระ · ทุกวัน", "Outstanding · all dates")}
                  </option>
                  <option value="draft">{copy("ฉบับร่าง", "Draft")}</option>
                  <option value="unpaid">{copy("ค้างชำระ", "Unpaid")}</option>
                  <option value="partial">
                    {copy("ชำระบางส่วน", "Partly Paid")}
                  </option>
                  <option value="paid">{copy("ชำระแล้ว", "Paid")}</option>
                  <option value="cancelled">
                    {copy("ยกเลิก", "Cancelled")}
                  </option>
                </select>
              </FilterField>
              <FilterField label={copy("ค้นหารายการ", "Search item")}>
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </FilterField>
            </FilterBar>
          </div>
          <DataTable
            caption={copy("รายการใบแจ้งหนี้ตัวอย่าง", "Sample invoices")}
            columns={[
              {
                key: "invoice",
                label: copy("ใบแจ้งหนี้ / ผู้ป่วย", "Invoice / patient"),
              },
              { key: "total", label: copy("ยอดรวม", "Total"), align: "end" },
              { key: "paid", label: copy("ชำระแล้ว", "Paid"), align: "end" },
              { key: "status", label: copy("สถานะ", "Status") },
              { key: "action", label: "" },
            ]}
            rows={rows.map((invoice) => [
              <span key="invoice">
                {invoice.id}
                <br />
                <span className="pc-note">
                  {patientLabel(invoice.patientId, locale)}
                </span>
              </span>,
              money(invoice.total),
              money(invoice.paid),
              <Badge key="status" tone={TONES[invoice.status]}>
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

        <Card title={copy("ขออนุมัติคืนเงินแยก", "Separate refund approval")} flush>
          <DataTable
            caption={copy("ขออนุมัติคืนเงินแยก", "Separate refund approval")}
            columns={[
              { key: "refund", label: copy("รายการรับเงิน", "Payment") },
              { key: "invoice", label: copy("ใบแจ้งหนี้", "Invoice") },
              {
                key: "amount",
                label: copy("จำนวนเงิน", "Amount"),
                align: "end",
              },
              { key: "status", label: copy("สถานะ", "Status") },
            ]}
            rows={refundRequestsFor().map((request) => [
              <span key="refund">
                {request.id}
                <br />
                <span className="pc-note">{request.reason}</span>
              </span>,
              request.invoiceId,
              money(request.amount),
              <Badge key="status" tone="warning">
                {t("status.pending")}
              </Badge>,
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>
      </div>
    </div>
  );
}
