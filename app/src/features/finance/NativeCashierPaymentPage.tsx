import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Card, DataTable, PageHeader } from "../../design/components";
import { useLocale } from "../../i18n/LocaleProvider";
import {
  listNativePayableInvoices,
  type NativePayableInvoice,
  PAYABLE_INVOICE_PAGE_SIZE,
  PayableInvoiceReadError,
} from "../../transport/native/payable-invoice";
import { FilterField } from "../operations/ops-ui";

const DEFAULT_CURRENCY = "THB";
type InvoiceRequest = { currency: string; search: string; offset: number };
type PaymentMode = "cash" | "card";

export function NativeCashierPaymentPage() {
  const { t } = useTranslation();
  const { status, session, authGeneration } = useSession();
  const [params] = useSearchParams();
  const canRead = status === "authenticated" && session?.mode === "live" &&
    session.user.roles.includes("CAS");
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
            key={`${authGeneration}:${params.toString()}`}
            initialSearch={params.get("invoice") ?? ""}
            initialMode={params.get("mode") === "card" ? "card" : "cash"}
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
}: { initialSearch: string; initialMode: PaymentMode }) {
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
    setRequest(next);
  }

  const outcome = result?.request === request ? result.outcome : null;
  const page = outcome && typeof outcome === "object" ? outcome : null;
  const selected = page?.rows.find((row) => row.id === selectedId);
  const { offset } = request;
  const amount = (value: number, currency: string) =>
    new Intl.NumberFormat(locale, { style: "currency", currency, currencyDisplay: "code" }).format(value);

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
}: {
  invoice?: NativePayableInvoice;
  initialMode: PaymentMode;
  amount: (value: number, currency: string) => string;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState(initialMode);
  const unavailableId = useId();
  const referenceHintId = useId();
  return (
    <Card title={t("nativePayment.review")} hint={invoice?.id}>
      <div className="ops-form">
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
                <select value={mode} onChange={(event) => setMode(event.target.value === "card" ? "card" : "cash")}>
                  <option value="cash">{t("financeWrite.modeCash")}</option>
                  <option value="card">{t("financeWrite.modeCard")}</option>
                </select>
              </FilterField>
              {mode === "card" && (
                <div>
                  <FilterField label={t("financeWrite.reference")}>
                    <input required autoComplete="off" aria-describedby={referenceHintId} />
                  </FilterField>
                  <p className="pc-field__hint" id={referenceHintId}>{t("nativePayment.referenceHint")}</p>
                </div>
              )}
            </div>
          </>
        ) : <p className="pc-note">{t("nativePayment.choose")}</p>}
        <div className="ops-form__actions">
          {/* Posting stays disconnected: the installed action accepts partial payments. */}
          <button
            type="button"
            className="pc-button pc-button--primary"
            disabled
            aria-describedby={unavailableId}
          >
            {t("nativePayment.confirm")}
          </button>
          <p className="pc-field__hint" id={unavailableId}>{t("nativePayment.confirmUnavailable")}</p>
        </div>
      </div>
    </Card>
  );
}
