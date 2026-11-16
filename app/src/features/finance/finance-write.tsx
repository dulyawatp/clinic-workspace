import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import {
  type InvoicePreview,
  type NativeFinanceWriteError,
  previewInvoice,
  receivePayment,
  submitInvoice,
} from "../../transport/native/finance-write";
import { useCapabilities } from "../shell/CapabilityProvider";

/**
 * CAS-02 / CAS-03 finance server writes.
 *
 * Each form renders only when bootstrap advertises its own capability, so the
 * demo surfaces keep their sample state until the deployment gate opens. The
 * invoice form has no rate and no total field: the server prices every line
 * from the installed selling price list and the posted total is re-read, so the
 * client's arithmetic is never truth. The discount input appears only when the
 * server's own preview reports the permission-gated path as available, and it
 * is an absolute amount — no rate is ever implied. The payment form posts one
 * Payment Entry of type Receive whose single allocation equals the paid amount;
 * no PAN/CVV field exists anywhere.
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

  if (get("write.invoice.submit") !== "enabled") return null;

  const amount = Number(qty);
  const discountAmount = Number(discount);
  const body = {
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

  async function review() {
    if (saving) return;
    if (invalid()) {
      setError(t("financeWrite.invalid"));
      return;
    }
    setError(null);
    setDone(null);
    setSaving(true);
    try {
      setPreview(await previewInvoice(body));
    } catch (failure) {
      setPreview(null);
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  async function submit() {
    if (saving) return;
    if (invalid()) {
      setError(t("financeWrite.invalid"));
      return;
    }
    setError(null);
    setDone(null);
    setSaving(true);
    try {
      const result = await submitInvoice(body);
      setDone(
        result.status === "submitted"
          ? t("financeWrite.invoiceDone", { total: result.total.toFixed(2) })
          : t("financeWrite.uncertain"),
      );
      setQty("");
      setPreview(null);
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
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
            onChange={setCustomer}
          />
          <Field
            id={`${id}-price-list`}
            label={t("financeWrite.priceList")}
            value={priceList}
            required
            onChange={setPriceList}
          />
          <Field
            id={`${id}-due-date`}
            label={t("financeWrite.dueDate")}
            value={dueDate}
            kind="date"
            required
            onChange={setDueDate}
          />
          <Field
            id={`${id}-item`}
            label={t("financeWrite.item")}
            value={item}
            required
            onChange={setItem}
          />
          <Field
            id={`${id}-qty`}
            label={t("financeWrite.qty")}
            value={qty}
            kind="number"
            required
            onChange={setQty}
          />
          {preview?.discount_available ? (
            <Field
              id={`${id}-discount`}
              label={t("financeWrite.discount")}
              value={discount}
              kind="number"
              hint={t("financeWrite.discountHint")}
              onChange={setDiscount}
            />
          ) : null}
        </div>
        <div className="ops-form__actions">
          <Button type="submit" disabled={saving}>
            {saving
              ? t("financeWrite.saving")
              : t("financeWrite.invoiceReview")}
          </Button>
          <Button type="button" disabled={saving} onClick={() => void submit()}>
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
    </section>
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
