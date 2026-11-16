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
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  allocationsFor,
  eligibleInvoices,
  invoiceById,
  invoiceOutstanding,
  patientLabel,
  paymentModeKey,
  voidedPaymentSample,
} from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

/**
 * CAS-03 payments. The form is the designed control set, but this build has no
 * payment contract: the post control is disabled with that reason, so no
 * Payment Entry, allocation or receipt is invented. Allocation figures come
 * from the fixture helpers (`dueOf` over posted payments), never from the
 * screen's own arithmetic on a total.
 */
export function PaymentsPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money, date } = useOpsFormat();
  const [params] = useSearchParams();
  const eligible = useMemo(() => eligibleInvoices(), []);
  const requested = params.get("invoice");
  const [invoiceId, setInvoiceId] = useState(
    eligible.some((invoice) => invoice.id === requested)
      ? (requested as string)
      : (eligible[0]?.id ?? ""),
  );
  const [mode, setMode] = useState<"cash" | "card">(
    params.get("mode") === "card" ? "card" : "cash",
  );
  const [reference, setReference] = useState("");

  const invoice = invoiceById(invoiceId);
  const due = invoice ? invoiceOutstanding(invoice) : 0;
  const [amount, setAmount] = useState(due.toFixed(2));
  const allocations = invoice ? allocationsFor(invoice.id) : [];
  const allocated = allocations.reduce((sum, row) => sum + row.amount, 0);
  const parsedAmount = Number(amount);
  const exceedsDue = Number.isFinite(parsedAmount) && parsedAmount > due;
  const remaining =
    Number.isFinite(parsedAmount) && !exceedsDue
      ? (invoice?.total ?? 0) - allocated - parsedAmount
      : null;
  const voided = voidedPaymentSample();

  if (!invoice) {
    return (
      <div data-surface="CAS-03" data-testid="payments-page">
        <PageHeader
          breadcrumb={
            <>
              <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
              <span aria-hidden="true">/</span>
              <span>CAS-03</span>
            </>
          }
          eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
          title={copy("รับเงินและใบเสร็จ", "Payments & receipts")}
        />
        <div className="pc-page__body">
          <OpsNote>
            {copy(
              "รายการที่เชื่อมแล้วไม่ออกใบแจ้งหนี้ซ้ำ ไม่ตัดสต็อกซ้ำจากหัตถการ",
              "Linked charges cannot be invoiced twice. No duplicate procedure stock deduction.",
            )}
          </OpsNote>
        </div>
      </div>
    );
  }

  return (
    <div data-surface="CAS-03" data-testid="payments-page">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>CAS-03</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("รับเงินและใบเสร็จ", "Payments & receipts")}
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
          title={copy("รับชำระเงินตามใบแจ้งหนี้", "Receive invoice payment")}
          hint={`${invoice.id} · ${patientLabel(invoice.patientId, locale)}`}
        >
          <div className="ops-form">
            <div className="ops-form__grid">
              <FilterField label={copy("ใบแจ้งหนี้", "Invoice")}>
                <select
                  value={invoice.id}
                  onChange={(event) => {
                    const next = event.target.value;
                    setInvoiceId(next);
                    const nextInvoice = invoiceById(next);
                    setAmount(
                      (nextInvoice ? invoiceOutstanding(nextInvoice) : 0).toFixed(
                        2,
                      ),
                    );
                  }}
                >
                  {eligible.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.id} · {money(invoiceOutstanding(row))}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField label={copy("วิธีชำระ", "Payment mode")}>
                <select
                  value={mode}
                  onChange={(event) =>
                    setMode(event.target.value as "cash" | "card")
                  }
                >
                  <option value="cash">{copy("เงินสด", "Cash")}</option>
                  <option value="card">
                    {copy("บัตรเครดิต", "Credit Card")}
                  </option>
                </select>
              </FilterField>
              <FilterField
                label={copy("จำนวนรับชำระ (THB)", "Received amount (THB)")}
              >
                <input
                  type="number"
                  min={0}
                  max={due}
                  step="0.01"
                  value={amount}
                  aria-describedby={
                    exceedsDue ? "payment-amount-limit" : undefined
                  }
                  onChange={(event) => setAmount(event.target.value)}
                />
              </FilterField>
              <FilterField
                label={copy("เลขอ้างอิงสลิป (บัตร)", "Terminal reference (card)")}
              >
                <input
                  value={reference}
                  onChange={(event) => setReference(event.target.value)}
                />
              </FilterField>
            </div>

            <dl className="pc-dl">
              <div>
                <dt>{copy("ยอดรวม", "Total")}</dt>
                <dd>{money(invoice.total)}</dd>
              </div>
              <div>
                <dt>{copy("จัดสรรแล้ว", "Allocated amount")}</dt>
                <dd>{money(allocated)}</dd>
              </div>
              <div>
                <dt>{copy("ค้างชำระ", "Outstanding")}</dt>
                <dd>
                  <strong>{money(due)}</strong>
                </dd>
              </div>
              <div>
                <dt>{copy("ยอดค้างหลังชำระ", "Balance after payment")}</dt>
                <dd>
                  {remaining === null
                    ? copy("ไม่พร้อมใช้งาน", "Unavailable")
                    : money(remaining)}
                </dd>
              </div>
            </dl>

            {exceedsDue ? (
              <p className="pc-field__hint" id="payment-amount-limit">
                {copy(
                  "จำนวนต้องมากกว่า 0 ไม่เกินยอดค้าง และบัตรต้องมีเลขอ้างอิงสลิป",
                  "Amount must be positive, not exceed outstanding; card requires terminal reference",
                )}
              </p>
            ) : null}

            <div className="ops-form__actions">
              <button
                type="button"
                className="pc-button pc-button--primary"
                disabled
                aria-describedby="payment-not-connected"
              >
                {copy(
                  "ตรวจทานและรับเงินตัวอย่าง",
                  "Review & post sample payment",
                )}
              </button>
              <span className="pc-field__hint" id="payment-not-connected">
                {t("checkout.writeUnavailable")}
              </span>
            </div>
            <OpsNote>
              {copy(
                "บัตรเครดิตเป็นวิธีชำระเท่านั้น ไม่เก็บ PAN/CVV ไม่เรียก gateway · บริษัทจากใบแจ้งหนี้ตัวอย่าง",
                "Card is a payment mode only. No PAN/CVV or gateway. Company comes from sample invoice.",
              )}
            </OpsNote>
          </div>
        </Card>

        <Card
          title={copy(
            "การรับเงินและใบเสร็จที่เชื่อม",
            "Linked posted payments & receipts",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "การรับเงินและใบเสร็จที่เชื่อม",
              "Linked posted payments & receipts",
            )}
            columns={[
              { key: "payment", label: copy("รายการรับเงิน", "Payment") },
              { key: "mode", label: copy("วิธี", "Mode") },
              {
                key: "date",
                label: copy("วันที่ตามปฏิทิน", "Calendar date"),
              },
              {
                key: "amount",
                label: copy("จัดสรรแล้ว", "Allocated amount"),
                align: "end",
              },
            ]}
            rows={allocations.map((row) => [
              row.id,
              t(paymentModeKey(row.mode)),
              date(row.date),
              money(row.amount),
            ])}
            empty={copy("ยังไม่มีรายการโพสต์", "No posted payments")}
          />
        </Card>

        <Card title={copy("รายการรับเงิน", "Payment")}>
          <p className="pc-status-line">
            {voided.id} · {voided.invoiceId} ·{" "}
            {t(paymentModeKey(voided.mode))} · {money(voided.amount)} ·{" "}
            <Badge tone="danger">{t("status.cancelled")}</Badge>
          </p>
          <p className="pc-note">{voided.cancellationReason}</p>
          <p className="pc-status-line ops-mono">{voided.reference}</p>
        </Card>
      </div>
    </div>
  );
}
