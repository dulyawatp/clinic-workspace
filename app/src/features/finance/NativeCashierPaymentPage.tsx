import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Card, DataTable, PageHeader } from "../../design/components";
import { useLocale } from "../../i18n/LocaleProvider";
import {
  financeWriteLocked,
  NativeFinanceWriteError,
  receivePayment,
  subscribeFinanceWriteLock,
} from "../../transport/native/finance-write";
import {
  listNativePayableInvoices,
  type NativePayableInvoice,
  PAYABLE_INVOICE_PAGE_SIZE,
  PayableInvoiceReadError,
} from "../../transport/native/payable-invoice";
import { getNativePaymentReceipt } from "../../transport/native/payment-receipt";
import { getNativePostedInvoiceDetail } from "../../transport/native/posted-invoice";
import { FilterField } from "../operations/ops-ui";

const DEFAULT_CURRENCY = "THB";
const MAX_BALANCE_FRACTION_DIGITS = 20;
const PAYABLE_STATUSES = new Set([
  "Submitted", "Unpaid", "Overdue", "Partly Paid",
  "Unpaid and Discounted", "Overdue and Discounted", "Partly Paid and Discounted",
]);
type InvoiceRequest = { currency: string; search: string; offset: number };
type PaymentMode = "cash" | "card";
type Receipt = { kind: "loading" | "unavailable" } | { kind: "ready"; href: string };
type Confirmation = { kind: "idle" | "checking" | "posting" | "uncertain" } |
  { kind: "confirmed"; payment: string; receipt: Receipt };
const isPaymentLocked = () => financeWriteLocked("payment_receive");

export function NativeCashierPaymentPage() {
  const { t } = useTranslation();
  const { status, session, authGeneration } = useSession();
  const [params] = useSearchParams();
  const canRead = status === "authenticated" && session?.mode === "live" &&
    session.user.roles.includes("CAS");
  const canSettle = canRead && session.paymentSettlement === "full_balance_v1" &&
    session.capabilities["write.payment.receive"] === "enabled";
  return (
    <div data-surface="CAS-03" data-testid="payments-page">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{t("inventoryRead.home")}</Link>
            <span aria-hidden="true">/</span>
            <span>CAS-03</span>
          </>
        }
        eyebrow={t("inventoryRead.source")}
        title={t("nav.payments")}
        subtitle={t("nativePayment.intro")}
      />
      <div className="pc-page__body">
        {canRead ? (
          <PayableInvoicePicker
            key={`${authGeneration}:${canSettle}:${params.toString()}`}
            initialSearch={params.get("invoice") ?? ""}
            initialMode={params.get("mode") === "card" ? "card" : "cash"}
            canSettle={canSettle}
          />
        ) : (
          <Card title={t("nativePayment.invoices")}>
            {status === "loading" ? (
              <p role="status" className="pc-note">{t("common.loading")}</p>
            ) : (
              <p role="alert" className="pc-error">
                {t(status === "error" ? "nativePayment.unavailable" : "nativePayment.permission")}
              </p>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

function PayableInvoicePicker({
  initialSearch,
  initialMode,
  canSettle,
}: { initialSearch: string; initialMode: PaymentMode; canSettle: boolean }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const [search, setSearch] = useState(initialSearch);
  const [request, setRequest] = useState<InvoiceRequest>({
    currency: DEFAULT_CURRENCY, search: initialSearch, offset: 0,
  });
  const [result, setResult] = useState<{
    request: InvoiceRequest;
    outcome: Awaited<ReturnType<typeof listNativePayableInvoices>> | "permission" | "unavailable";
  } | null>(null);
  const [observedCurrencies, setObservedCurrencies] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [staleIds, setStaleIds] = useState<string[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    void listNativePayableInvoices({
      ...request,
      currency: request.currency || undefined,
    }, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        setResult({ request, outcome: page });
        setObservedCurrencies((seen) =>
          [...new Set([...seen, ...page.rows.map((row) => row.currency)])].sort(),
        );
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setResult({
          request,
          outcome: error instanceof PayableInvoiceReadError ? error.kind : "unavailable",
        });
      },
    );
    return () => controller.abort();
  }, [request]);

  function load(next: InvoiceRequest) {
    setSelectedId("");
    setStaleIds([]);
    setRequest(next);
  }

  const outcome = result?.request === request ? result.outcome : null;
  const page = outcome && typeof outcome === "object" ? outcome : null;
  const selected = page?.rows.find((row) => row.id === selectedId);
  const { offset } = request;
  const amount = (value: number, currency: string) =>
    new Intl.NumberFormat(locale, {
      style: "currency", currency, currencyDisplay: "code",
      maximumFractionDigits: MAX_BALANCE_FRACTION_DIGITS,
    }).format(value);

  return (
    <>
      <Card
        title={t("nativePayment.invoices")}
        hint={t("nativePayment.filterHint")}
        footer={offset > 0 || page?.hasMore ? (
          <>
            <button
              className="pc-button pc-button--secondary pc-button--sm"
              type="button"
              disabled={offset === 0}
              onClick={() => load({ ...request, offset: offset - PAYABLE_INVOICE_PAGE_SIZE })}
            >
              {t("inventoryRead.previous")}
            </button>
            <span className="pc-note">
              {t("inventoryRead.page", { page: offset / PAYABLE_INVOICE_PAGE_SIZE + 1 })}
            </span>
            <button
              className="pc-button pc-button--secondary pc-button--sm"
              type="button"
              disabled={!page?.hasMore}
              onClick={() => load({ ...request, offset: offset + PAYABLE_INVOICE_PAGE_SIZE })}
            >
              {t("action.next")}
            </button>
          </>
        ) : undefined}
      >
        <div className="ops-form">
          <form
            className="ops-filters"
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              load({ ...request, search: search.trim(), offset: 0 });
            }}
          >
            <FilterField label={t("financeWrite.detailCurrency")}>
              <select
                value={request.currency}
                onChange={(event) => load({ currency: event.target.value, search: search.trim(), offset: 0 })}
              >
                <option value={DEFAULT_CURRENCY}>{DEFAULT_CURRENCY}</option>
                {observedCurrencies.filter((currency) => currency !== DEFAULT_CURRENCY).map((currency) => (
                  <option key={currency} value={currency}>{currency}</option>
                ))}
                <option value="">{t("nativePayment.allCurrencies")}</option>
              </select>
            </FilterField>
            <FilterField label={t("nativePayment.search")}>
              <input
                type="search"
                maxLength={1024}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </FilterField>
            <button className="pc-button pc-button--secondary" type="submit">
              {t("action.search")}
            </button>
          </form>
          {page ? (
            <DataTable
              caption={t("nativePayment.invoices")}
              columns={[
                { key: "invoice", label: t("nativePayment.invoice") },
                { key: "customer", label: t("nativePayment.customer") },
                { key: "patient", label: t("field.patient") },
                { key: "date", label: t("nativePayment.date") },
                { key: "due", label: t("nativePayment.outstanding"), align: "end" },
              ]}
              rows={page.rows.map((row) => [
                <button
                  key="invoice"
                  type="button"
                  className={`pc-button pc-button--${row.id === selectedId ? "primary" : "secondary"} pc-button--sm ops-mono`}
                  aria-label={t("nativePayment.selectInvoice", { id: row.id })}
                  aria-pressed={row.id === selectedId}
                  onClick={() => setSelectedId(row.id)}
                >
                  {row.id}
                </button>,
                <BillingCustomer key="customer" invoice={row} />,
                <span key="patient" className="pc-note">{t("nativePayment.patientDetailsUnavailable")}</span>,
                row.postingDate,
                amount(row.outstanding, row.currency),
              ])}
              empty={t("nativePayment.empty")}
            />
          ) : typeof outcome === "string" ? (
            <p role="alert" className="pc-error">{t(`nativePayment.${outcome}`)}</p>
          ) : (
            <p role="status" className="pc-note">{t("common.loading")}</p>
          )}
        </div>
      </Card>
      <PaymentReview
        key={selected?.id ?? ""}
        invoice={selected}
        initialMode={initialMode}
        amount={amount}
        canSettle={canSettle}
        stale={staleIds.includes(selectedId)}
        markStale={(id) => setStaleIds((ids) => [...ids, id])}
        refresh={() => load({ ...request })}
      />
    </>
  );
}

function BillingCustomer({ invoice }: { invoice: NativePayableInvoice }) {
  return invoice.customerName && invoice.customerName !== invoice.customer ? (
    <span>
      {invoice.customerName}<br />
      <span className="pc-note ops-mono">{invoice.customer}</span>
    </span>
  ) : <span className="ops-mono">{invoice.customer}</span>;
}

function PaymentReview({
  invoice,
  initialMode,
  amount,
  canSettle,
  stale,
  markStale,
  refresh,
}: {
  invoice?: NativePayableInvoice;
  initialMode: PaymentMode;
  amount: (value: number, currency: string) => string;
  canSettle: boolean;
  stale: boolean;
  markStale: (id: string) => void;
  refresh: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState(initialMode);
  const [reference, setReference] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation>({ kind: "idle" });
  const attempt = useRef<AbortController | null>(null);
  const unavailableId = useId();
  const referenceHintId = useId();
  const locked = useSyncExternalStore(subscribeFinanceWriteLock, isPaymentLocked);
  const busy = confirmation.kind === "checking" || confirmation.kind === "posting";
  const uncertain = confirmation.kind === "uncertain" || (locked && !busy && confirmation.kind !== "confirmed");
  const invalidBalance = invoice && (!Number.isFinite(invoice.outstanding) ||
    invoice.outstanding <= 0 || Number(invoice.outstanding.toFixed(MAX_BALANCE_FRACTION_DIGITS)) !== invoice.outstanding);
  const stopped = canSettle && (busy || stale || uncertain || confirmation.kind === "confirmed");
  const disabled = !canSettle || !invoice || invalidBalance || stopped || (mode === "card" && !reference.trim());

  // Invalidate the review synchronously when the selected invoice or auth
  // view unmounts. A dispatched write continues under its captured auth context.
  useLayoutEffect(() => () => attempt.current?.abort(), []);

  async function confirm() {
    if (disabled || !invoice || attempt.current || isPaymentLocked()) return;
    const controller = new AbortController();
    attempt.current = controller;
    const { signal } = controller;
    setConfirmation({ kind: "checking" });

    const current = await getNativePostedInvoiceDetail(invoice.id, signal).catch(() => null);
    if (signal.aborted) return;
    if (!current || current.id !== invoice.id || !PAYABLE_STATUSES.has(current.status) ||
      current.currency !== invoice.currency || current.outstanding !== invoice.outstanding) {
      markStale(invoice.id);
      setConfirmation({ kind: "idle" });
      return;
    }

    const paymentMode = mode === "card" ? "Credit Card" : "Cash";
    let payment: string;
    setConfirmation({ kind: "posting" });
    try {
      const result = await receivePayment({
        operation_key: crypto.randomUUID(),
        invoice: current.id,
        amount: current.outstanding,
        allocations: [{ invoice: current.id, amount: current.outstanding }],
        mode_of_payment: paymentMode,
        ...(mode === "card" ? { reference_no: reference.trim() } : {}),
      }, { expectFullSettlement: true });
      payment = result.payment;
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof NativeFinanceWriteError && error.code === "validation") {
        markStale(invoice.id);
        setConfirmation({ kind: "idle" });
      } else {
        setConfirmation({ kind: "uncertain" });
      }
      return;
    }

    if (signal.aborted) return;
    setConfirmation({ kind: "confirmed", payment, receipt: { kind: "loading" } });
    const href = await getNativePaymentReceipt({
      payment,
      invoice: current.id,
      amount: current.outstanding,
      currency: current.currency,
      mode: paymentMode,
    }, signal).catch(() => null);
    if (signal.aborted) return;
    setConfirmation({
      kind: "confirmed", payment,
      receipt: href ? { kind: "ready", href } : { kind: "unavailable" },
    });
  }

  return (
    <Card title={t("nativePayment.review")} hint={invoice?.id}>
      <form className="ops-form" onSubmit={(event) => {
        event.preventDefault();
        void confirm();
      }}>
        {invoice ? (
          <>
            <dl className="pc-dl">
              <div>
                <dt>{t("nativePayment.customer")}</dt>
                <dd><BillingCustomer invoice={invoice} /></dd>
              </div>
              <div>
                <dt>{t("field.patient")}</dt>
                <dd>{t("nativePayment.patientDetailsUnavailable")}</dd>
              </div>
              <div>
                <dt>{t("nativePayment.date")}</dt>
                <dd>{invoice.postingDate}</dd>
              </div>
              <div>
                <dt>{t("nativePayment.total")}</dt>
                <dd>{amount(invoice.total, invoice.currency)}</dd>
              </div>
            </dl>
            <div className="ops-form__grid">
              <FilterField label={t("nativePayment.fullAmount")}>
                <input readOnly value={amount(invoice.outstanding, invoice.currency)} />
              </FilterField>
              <FilterField label={t("nativePayment.method")}>
                <select
                  value={mode}
                  disabled={stopped}
                  onChange={(event) => {
                    setReference("");
                    setMode(event.target.value === "card" ? "card" : "cash");
                  }}
                >
                  <option value="cash">{t("financeWrite.modeCash")}</option>
                  <option value="card">{t("financeWrite.modeCard")}</option>
                </select>
              </FilterField>
              {mode === "card" && (
                <div>
                  <FilterField label={t("financeWrite.reference")}>
                    <input
                      required
                      autoComplete="off"
                      maxLength={140}
                      disabled={stopped}
                      value={reference}
                      onChange={(event) => setReference(event.target.value)}
                      aria-describedby={referenceHintId}
                    />
                  </FilterField>
                  <p className="pc-field__hint" id={referenceHintId}>{t("nativePayment.referenceHint")}</p>
                </div>
              )}
            </div>
          </>
        ) : <p className="pc-note">{t("nativePayment.choose")}</p>}
        <div className="ops-form__actions">
          <button
            type="submit"
            className="pc-button pc-button--primary"
            disabled={disabled}
            aria-describedby={!canSettle ? unavailableId : undefined}
          >
            {t("nativePayment.confirm")}
          </button>
          {!canSettle && (
            <p className="pc-field__hint" id={unavailableId}>{t("nativePayment.confirmUnavailable")}</p>
          )}
          {canSettle && (stale || invalidBalance) && !uncertain && (
            <>
              <p className="pc-error" role="alert">{t("nativePayment.refreshReview")}</p>
              <button type="button" className="pc-button pc-button--secondary" onClick={refresh}>
                {t("nativePayment.refreshInvoices")}
              </button>
            </>
          )}
        </div>
        {canSettle && uncertain && <p className="pc-error" role="alert">{t("financeWrite.uncertain")}</p>}
        {busy && <p className="pc-note" role="status">
          {t(confirmation.kind === "checking" ? "nativePayment.checkingBalance" : "financeWrite.saving")}
        </p>}
        {confirmation.kind === "confirmed" && (
          <>
            <p className="pc-note" role="status">{t("nativePayment.confirmed", { payment: confirmation.payment })}</p>
            {confirmation.receipt.kind === "ready" ? (
              <div className="ops-form__actions">
                <a className="pc-button pc-button--secondary" href={confirmation.receipt.href} target="_blank" rel="noopener noreferrer">
                  {t("nativePayment.openReceipt")}
                </a>
              </div>
            ) : (
              <p className="pc-note" role="status">
                {t(confirmation.receipt.kind === "loading" ? "nativePayment.checkingReceipt" : "nativePayment.receiptUnavailable")}
              </p>
            )}
          </>
        )}
      </form>
    </Card>
  );
}
