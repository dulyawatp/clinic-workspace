import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import { useCapabilities } from "../shell/CapabilityProvider";
import {
  draftPurchaseOrder,
  receivePurchaseOrder,
  submitPurchaseOrder,
  type NativePurchaseWriteError,
} from "../../transport/native/purchase-write";

/**
 * PHA-07 / PHA-05 purchasing server writes.
 *
 * Each form renders only when bootstrap advertises its own capability, so the
 * demo surfaces keep their sample state until the deployment gate opens. The
 * forms send a supplier, one itemized line (item, quantity, rate) or a
 * purchase-order reference with a quantity; every decision — the currency and
 * company, over-receipt, the PO pending balance, batch validity and the status
 * transition — stays server-side. Creating a draft never submits it.
 */

function failureKey(error: unknown): string {
  const code = (error as NativePurchaseWriteError | null)?.code;
  if (code === "validation") return "purchaseWrite.validation";
  if (code === "forbidden") return "purchaseWrite.forbidden";
  if (code === "conflict") return "purchaseWrite.conflict";
  if (code === "auth") return "purchaseWrite.auth";
  return "purchaseWrite.uncertain";
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
  onChange: (value: string) => void;
}

function Field({
  id,
  label,
  value,
  kind = "text",
  maxLength = 140,
  required = false,
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
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

interface FormShellProps {
  titleKey: string;
  testId?: string;
  noticeKey: string;
  submitKey: string;
  doneKey: string;
  children: React.ReactNode;
  onSubmit: () => Promise<void>;
  done: boolean;
  error: string | null;
  saving: boolean;
}

function FormShell({
  titleKey,
  testId = "purchase-write-form",
  noticeKey,
  submitKey,
  doneKey,
  children,
  onSubmit,
  done,
  error,
  saving,
}: FormShellProps) {
  const { t } = useTranslation();
  return (
    <section
      className="admin-write"
      aria-label={t(titleKey)}
      data-testid={testId}
    >
      <h3>{t(titleKey)}</h3>
      <p className="pc-note">{t(noticeKey)}</p>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit();
        }}
      >
        <div className="admin-write-fields">{children}</div>
        <div className="ops-form__actions">
          <Button type="submit" disabled={saving}>
            {saving ? t("purchaseWrite.saving") : t(submitKey)}
          </Button>
        </div>
      </form>
      {done ? (
        <p role="status" className="ops-note">
          {t(doneKey)}
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

export function PurchaseOrderDraftForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [supplier, setSupplier] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [rate, setRate] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.purchase_order.draft") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    const amount = Number(qty);
    const price = Number(rate);
    if (
      !supplier.trim() ||
      !item.trim() ||
      !Number.isFinite(amount) ||
      amount <= 0 ||
      !Number.isFinite(price) ||
      price <= 0
    ) {
      setError(t("purchaseWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await draftPurchaseOrder({
        operation_key: newOperationKey(),
        supplier: supplier.trim(),
        items: [
          { item_code: item.trim(), qty: amount, rate: price },
        ],
      });
      setDone(true);
      setQty("");
      setRate("");
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <FormShell
      titleKey="purchaseWrite.draftTitle"
      noticeKey="purchaseWrite.draftNotice"
      submitKey="purchaseWrite.draftSubmit"
      doneKey="purchaseWrite.draftDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-supplier`}
        label={t("purchaseWrite.supplier")}
        value={supplier}
        required
        onChange={setSupplier}
      />
      <Field
        id={`${id}-item`}
        label={t("purchaseWrite.item")}
        value={item}
        required
        onChange={setItem}
      />
      <Field
        id={`${id}-qty`}
        label={t("purchaseWrite.qty")}
        value={qty}
        kind="number"
        required
        onChange={setQty}
      />
      <Field
        id={`${id}-rate`}
        label={t("purchaseWrite.rate")}
        value={rate}
        kind="number"
        required
        onChange={setRate}
      />
    </FormShell>
  );
}

export function PurchaseOrderSubmitForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [order, setOrder] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.purchase_order.submit") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    if (!order.trim()) {
      setError(t("purchaseWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await submitPurchaseOrder({
        operation_key: newOperationKey(),
        purchase_order: order.trim(),
      });
      setDone(true);
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <FormShell
      testId="purchase-submit-form"
      titleKey="purchaseWrite.submitTitle"
      noticeKey="purchaseWrite.submitNotice"
      submitKey="purchaseWrite.submitSubmit"
      doneKey="purchaseWrite.submitDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-order`}
        label={t("purchaseWrite.order")}
        value={order}
        required
        onChange={setOrder}
      />
    </FormShell>
  );
}

export function PurchaseReceiptForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [order, setOrder] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [batch, setBatch] = useState("");
  const [expiry, setExpiry] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.stock.receive") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    const amount = Number(qty);
    if (
      !order.trim() ||
      !item.trim() ||
      !warehouse.trim() ||
      !Number.isFinite(amount) ||
      amount <= 0
    ) {
      setError(t("purchaseWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await receivePurchaseOrder({
        operation_key: newOperationKey(),
        purchase_order: order.trim(),
        item_code: item.trim(),
        qty: amount,
        warehouse: warehouse.trim(),
        batch_id: batch.trim() || undefined,
        batch_expiry: expiry.trim() || undefined,
      });
      setDone(true);
      setQty("");
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <FormShell
      testId="purchase-receipt-form"
      titleKey="purchaseWrite.receiptTitle"
      noticeKey="purchaseWrite.receiptNotice"
      submitKey="purchaseWrite.receiptSubmit"
      doneKey="purchaseWrite.receiptDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-order`}
        label={t("purchaseWrite.order")}
        value={order}
        required
        onChange={setOrder}
      />
      <Field
        id={`${id}-item`}
        label={t("purchaseWrite.item")}
        value={item}
        required
        onChange={setItem}
      />
      <Field
        id={`${id}-qty`}
        label={t("purchaseWrite.receiveQty")}
        value={qty}
        kind="number"
        required
        onChange={setQty}
      />
      <Field
        id={`${id}-warehouse`}
        label={t("purchaseWrite.receiveWarehouse")}
        value={warehouse}
        required
        onChange={setWarehouse}
      />
      <Field
        id={`${id}-batch`}
        label={t("purchaseWrite.batch")}
        value={batch}
        onChange={setBatch}
      />
      <Field
        id={`${id}-expiry`}
        label={t("purchaseWrite.batchExpiry")}
        value={expiry}
        kind="date"
        onChange={setExpiry}
      />
    </FormShell>
  );
}
