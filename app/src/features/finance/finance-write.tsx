import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../design/components";
import { nativeTransportEnabled } from "../../transport/native/flag";
import {
  type NativeFinanceWriteError,
  receivePayment,
} from "../../transport/native/finance-write";
import {
  type InvoicePreview,
  type NativeInvoiceRecord,
  findInvoices,
  InvoiceWriteInFlightError,
  InvoiceWriteRejectedError,
  previewInvoice,
  submitInvoice,
  viewInvoice,
} from "../../transport/native/invoice-write";
import { useCapabilities } from "../shell/CapabilityProvider";

/**
 * CAS-02 / CAS-03 finance server writes.
 *
 * Each form renders only when bootstrap advertises its own capability, so the
 * demo surfaces keep their sample state until the deployment gate opens. The
 * invoice form uses the converted native lane: one server preview and then one
 * submit, with no browser lock, no acknowledgement and no automatic retry. It
 * has no rate and no total field — the server prices every line from the
 * installed selling price list and the posted total is re-read, so the client's
 * arithmetic is never truth. The discount input appears only when the server's
 * own preview reports the permission-gated path as available, and it is an
 * absolute amount — no rate is ever implied. An unknown invoice outcome stops
 * the mounted form instead of offering a repeat. The payment form keeps its
 * fenced lane: one Payment Entry of type Receive whose single allocation equals
 * the paid amount, with no PAN/CVV field anywhere.
 */

function failureKey(error: unknown): string {
  const code = (error as NativeFinanceWriteError | null)?.code;
  if (code === "validation") return "financeWrite.validation";
  if (code === "forbidden") return "financeWrite.forbidden";
  if (code === "conflict") return "financeWrite.conflict";
  if (code === "auth") return "financeWrite.auth";
  return "financeWrite.uncertain";
}

function newOperationKey(): string {
  return crypto.randomUUID();
}

interface FieldProps {
  id: string;
  label: string;
  value: string;
  kind?: "text" | "number" | "date";
  maxLength?: number;
  required?: boolean;
  hint?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}

function Field({
  id,
  label,
  value,
  kind = "text",
  maxLength = 140,
  required = false,
  hint,
  disabled = false,
  onChange,
}: FieldProps) {
  return (
    <div className="admin-write-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={kind}
        maxLength={kind === "text" ? maxLength : undefined}
        value={value}
        required={required}
        disabled={disabled}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? (
        <p className="pc-field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function InvoiceSubmitForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const { session } = useSession();
  const id = useId();
  const [customer, setCustomer] = useState("");
  const [priceList, setPriceList] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [discount, setDiscount] = useState("");
  const [preview, setPreview] = useState<InvoicePreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /**
   * A lost or unreadable submit response may already have posted the invoice.
   * The mounted form stops: no second dispatch, no acknowledgement, no retry.
   */
  const [stopped, setStopped] = useState(false);
  const [lookupId, setLookupId] = useState("");
  const [results, setResults] = useState<NativeInvoiceRecord[] | null>(null);
  const [lookupError, setLookupError] = useState(false);
  const [looking, setLooking] = useState(false);

  const canWrite = get("write.invoice.submit") === "enabled";
  const canLookup = nativeTransportEnabled() && !!session?.user.roles.includes("CAS");
  if (!canWrite && !canLookup) return null;

  async function lookup(kind: "customer" | "invoice") {
    const value = (kind === "customer" ? customer : lookupId).trim();
    if (!value || looking) return;
    setLooking(true);
    setResults(null);
    setLookupError(false);
    try {
      setResults(kind === "customer" ? await findInvoices(value) : [await viewInvoice(value)]);
    } catch {
      setLookupError(true);
    } finally {
      setLooking(false);
    }
  }

  const amount = Number(qty);
  const discountAmount = Number(discount);

  function payload() {
    return {
      operation_key: newOperationKey(),
      customer: customer.trim(),
      selling_price_list: priceList.trim(),
      due_date: dueDate.trim(),
      items: [
        { item_code: item.trim(), qty: Number.isFinite(amount) ? amount : 0 },
      ],
      ...(preview?.discount_available &&
      Number.isFinite(discountAmount) &&
      discountAmount > 0
        ? { discount: discountAmount }
        : {}),
    };
  }

  function invalid(): boolean {
    return (
      !customer.trim() ||
      !priceList.trim() ||
      !dueDate.trim() ||
      !item.trim() ||
      !Number.isFinite(amount) ||
      amount <= 0
    );
  }

  function rejectedKey(failure: InvoiceWriteRejectedError): string {
    return `financeWrite.${failure.kind}`;
  }

  async function review() {
    if (saving || stopped) return;
    if (invalid()) {
      setError(t("financeWrite.invalid"));
      return;
    }
    setError(null);
    setDone(null);
    setSaving(true);
    try {
      setPreview(await previewInvoice(payload()));
    } catch (failure) {
      setPreview(null);
      setError(
        t(
          failure instanceof InvoiceWriteRejectedError
            ? rejectedKey(failure)
            : "financeWrite.uncertain",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  async function submit() {
    if (saving || stopped) return;
    if (invalid()) {
      setError(t("financeWrite.invalid"));
      return;
    }
    setError(null);
    setDone(null);
    setSaving(true);
    const submission = payload();
    try {
      const result = await submitInvoice(submission);
      setDone(
        t("financeWrite.invoiceDone", { total: result.total.toFixed(2) }),
      );
      setQty("");
      setPreview(null);
    } catch (failure) {
      if (failure instanceof InvoiceWriteInFlightError) {
        // The same form is already dispatching; the first outcome decides.
        return;
      }
      if (failure instanceof InvoiceWriteRejectedError) {
        setError(t(rejectedKey(failure)));
      } else {
        setCustomer(submission.customer);
        setStopped(true);
        setError(t("financeWrite.uncertain"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
    {canWrite ? <section
      className="admin-write"
      aria-label={t("financeWrite.invoiceTitle")}
      data-testid="invoice-write-form"
    >
      <h3>{t("financeWrite.invoiceTitle")}</h3>
      <p className="pc-note">{t("financeWrite.invoiceNotice")}</p>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void review();
        }}
      >
        <div className="admin-write-fields">
          <Field
            id={`${id}-customer`}
            label={t("financeWrite.customer")}
            value={customer}
            required
            disabled={stopped}
            onChange={setCustomer}
          />
          <Field
            id={`${id}-price-list`}
            label={t("financeWrite.priceList")}
            value={priceList}
            required
            disabled={stopped}
            onChange={setPriceList}
          />
          <Field
            id={`${id}-due-date`}
            label={t("financeWrite.dueDate")}
            value={dueDate}
            kind="date"
            required
            disabled={stopped}
            onChange={setDueDate}
          />
          <Field
            id={`${id}-item`}
            label={t("financeWrite.item")}
            value={item}
            required
            disabled={stopped}
            onChange={setItem}
          />
          <Field
            id={`${id}-qty`}
            label={t("financeWrite.qty")}
            value={qty}
            kind="number"
            required
            disabled={stopped}
            onChange={setQty}
          />
          {preview?.discount_available ? (
            <Field
              id={`${id}-discount`}
              label={t("financeWrite.discount")}
              value={discount}
              kind="number"
              hint={t("financeWrite.discountHint")}
              disabled={stopped}
              onChange={setDiscount}
            />
          ) : null}
        </div>
        <div className="ops-form__actions">
          <Button type="submit" disabled={saving || stopped}>
            {saving
              ? t("financeWrite.saving")
              : t("financeWrite.invoiceReview")}
          </Button>
          <Button
            type="button"
            disabled={saving || stopped}
            onClick={() => void submit()}
          >
            {t("financeWrite.invoiceSubmit")}
          </Button>
        </div>
      </form>

      {preview ? (
        <dl className="pc-dl" data-testid="invoice-preview">
          <div>
            <dt>{t("financeWrite.netTotal")}</dt>
            <dd>{preview.net_total.toFixed(2)}</dd>
          </div>
          <div>
            <dt>{t("financeWrite.discount")}</dt>
            <dd>{preview.discount.toFixed(2)}</dd>
          </div>
          <div>
            <dt>{t("financeWrite.totalTaxes")}</dt>
            <dd>{preview.total_taxes.toFixed(2)}</dd>
          </div>
          <div>
            <dt>{t("financeWrite.total")}</dt>
            <dd>
              <strong>{preview.total.toFixed(2)}</strong>
            </dd>
          </div>
        </dl>
      ) : null}

      {done ? (
        <p role="status" className="ops-note">
          {done}
        </p>
      ) : null}
      {error ? (
        <p className="pc-error" role="alert">
          {error}
        </p>
      ) : null}
    </section> : null}
    {canLookup ? (
      <section className="admin-write" aria-label={t("financeWrite.lookupTitle")}>
        <h3>{t("financeWrite.lookupTitle")}</h3>
        <p className="pc-note">{t("financeWrite.lookupNotice")}</p>
        <div className="admin-write-fields">
          <Field id={`${id}-lookup-customer`} label={t("financeWrite.lookupCustomer")} value={customer} onChange={setCustomer} disabled={stopped} />
          <Field id={`${id}-lookup-invoice`} label={t("financeWrite.lookupInvoice")} value={lookupId} onChange={setLookupId} />
        </div>
        <div className="ops-form__actions">
          <Button type="button" disabled={looking || !customer.trim()} onClick={() => void lookup("customer")}>{t("financeWrite.lookupCustomerAction")}</Button>
          <Button type="button" disabled={looking || !lookupId.trim()} onClick={() => void lookup("invoice")}>{t("financeWrite.lookupInvoiceAction")}</Button>
        </div>
        {looking ? <p role="status">{t("financeWrite.lookupLoading")}</p> : null}
        {lookupError ? <p role="alert" className="pc-error">{t("financeWrite.lookupError")}</p> : null}
        {results ? <div role="status">
          {results.length === 0 ? <p>{t("financeWrite.lookupEmpty")}</p> : (
            <ul>{results.map((record) => <li key={record.invoice_id}>{record.invoice_id} · {record.status} · {t("financeWrite.lookupDocstatus")}: {record.docstatus}</li>)}</ul>
          )}
        </div> : null}
      </section>
    ) : null}
    </>
  );
}

export function PaymentReceiveForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [invoice, setInvoice] = useState("");
  const [mode, setMode] = useState<"Cash" | "Credit Card">("Cash");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (get("write.payment.receive") !== "enabled") return null;

  const paid = Number(amount);

  function invalid(): boolean {
    return (
      !invoice.trim() ||
      !Number.isFinite(paid) ||
      paid <= 0 ||
      (mode === "Credit Card" && !reference.trim())
    );
  }

  async function submit() {
    if (saving) return;
    if (invalid()) {
      setError(t("financeWrite.invalidPayment"));
      return;
    }
    setError(null);
    setDone(null);
    setSaving(true);
    try {
      const result = await receivePayment({
        operation_key: newOperationKey(),
        invoice: invoice.trim(),
        mode_of_payment: mode,
        amount: paid,
        allocations: [{ invoice: invoice.trim(), amount: paid }],
        reference_no: mode === "Credit Card" ? reference.trim() : undefined,
      });
      setDone(
        result.status === "received"
          ? t("financeWrite.paymentDone", {
              outstanding: result.outstanding.toFixed(2),
            })
          : t("financeWrite.uncertain"),
      );
      setAmount("");
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      className="admin-write"
      aria-label={t("financeWrite.paymentTitle")}
      data-testid="payment-write-form"
    >
      <h3>{t("financeWrite.paymentTitle")}</h3>
      <p className="pc-note">{t("financeWrite.paymentNotice")}</p>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="admin-write-fields">
          <Field
            id={`${id}-invoice`}
            label={t("financeWrite.invoice")}
            value={invoice}
            required
            onChange={setInvoice}
          />
          <div className="admin-write-field">
            <label htmlFor={`${id}-mode`}>{t("financeWrite.mode")}</label>
            <select
              id={`${id}-mode`}
              value={mode}
              onChange={(event) =>
                setMode(event.target.value as "Cash" | "Credit Card")
              }
            >
              <option value="Cash">{t("financeWrite.modeCash")}</option>
              <option value="Credit Card">{t("financeWrite.modeCard")}</option>
            </select>
          </div>
          <Field
            id={`${id}-amount`}
            label={t("financeWrite.amount")}
            value={amount}
            kind="number"
            required
            hint={t("financeWrite.allocationHint")}
            onChange={setAmount}
          />
          {mode === "Credit Card" ? (
            <Field
              id={`${id}-reference`}
              label={t("financeWrite.reference")}
              value={reference}
              required
              hint={t("financeWrite.referenceHint")}
              onChange={setReference}
            />
          ) : null}
        </div>
        <div className="ops-form__actions">
          <Button type="submit" disabled={saving}>
            {saving
              ? t("financeWrite.saving")
              : t("financeWrite.paymentSubmit")}
          </Button>
        </div>
      </form>
      {done ? (
        <p role="status" className="ops-note">
          {done}
        </p>
      ) : null}
      {error ? (
        <p className="pc-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
