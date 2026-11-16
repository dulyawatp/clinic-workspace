import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import { useCapabilities } from "../shell/CapabilityProvider";
import {
  adjustNativeStock,
  receiveNativeStock,
  transferNativeStock,
  type NativeStockWriteError,
} from "../../transport/native/stock-write";

/**
 * PHA-05 / PHA-06 / ADM-RECONCILE server writes.
 *
 * Each form renders only when bootstrap advertises its own capability, so the
 * demo surfaces keep their sample state until the deployment gate opens. The
 * forms send masters, a quantity and (for a new batch) its expiry; every
 * decision — batch validity, stock sufficiency, over-receipt, the restricted
 * reconciliation account — stays server-side.
 */

function failureKey(error: unknown): string {
  const code = (error as NativeStockWriteError | null)?.code;
  if (code === "validation") return "stockWrite.validation";
  if (code === "forbidden") return "stockWrite.forbidden";
  if (code === "conflict") return "stockWrite.conflict";
  if (code === "auth") return "stockWrite.auth";
  return "stockWrite.uncertain";
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
      data-testid="stock-write-form"
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
            {saving ? t("stockWrite.saving") : t(submitKey)}
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

export function ReceiveStockForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [supplier, setSupplier] = useState("");
  const [batch, setBatch] = useState("");
  const [expiry, setExpiry] = useState("");
  const [order, setOrder] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.stock.receive") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    const amount = Number(qty);
    if (
      !item.trim() ||
      !warehouse.trim() ||
      !supplier.trim() ||
      !Number.isFinite(amount) ||
      amount <= 0
    ) {
      setError(t("stockWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await receiveNativeStock({
        operation_key: newOperationKey(),
        item_code: item.trim(),
        qty: amount,
        warehouse: warehouse.trim(),
        supplier: supplier.trim(),
        batch_id: batch.trim() || undefined,
        batch_expiry: expiry.trim() || undefined,
        purchase_order: order.trim() || undefined,
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
      titleKey="stockWrite.receiveTitle"
      noticeKey="stockWrite.receiveNotice"
      submitKey="stockWrite.receiveSubmit"
      doneKey="stockWrite.receiveDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-item`}
        label={t("stockWrite.item")}
        value={item}
        required
        onChange={setItem}
      />
      <Field
        id={`${id}-qty`}
        label={t("stockWrite.qty")}
        value={qty}
        kind="number"
        required
        onChange={setQty}
      />
      <Field
        id={`${id}-warehouse`}
        label={t("stockWrite.warehouse")}
        value={warehouse}
        required
        onChange={setWarehouse}
      />
      <Field
        id={`${id}-supplier`}
        label={t("stockWrite.supplier")}
        value={supplier}
        required
        onChange={setSupplier}
      />
      <Field
        id={`${id}-batch`}
        label={t("stockWrite.batch")}
        value={batch}
        onChange={setBatch}
      />
      <Field
        id={`${id}-expiry`}
        label={t("stockWrite.batchExpiry")}
        value={expiry}
        kind="date"
        onChange={setExpiry}
      />
      <Field
        id={`${id}-po`}
        label={t("stockWrite.purchaseOrder")}
        value={order}
        onChange={setOrder}
      />
    </FormShell>
  );
}

export function TransferStockForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [batch, setBatch] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.stock.transfer") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    const amount = Number(qty);
    if (
      !item.trim() ||
      !source.trim() ||
      !target.trim() ||
      source.trim() === target.trim() ||
      !Number.isFinite(amount) ||
      amount <= 0
    ) {
      setError(t("stockWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await transferNativeStock({
        operation_key: newOperationKey(),
        item_code: item.trim(),
        qty: amount,
        source_warehouse: source.trim(),
        target_warehouse: target.trim(),
        batch_id: batch.trim() || undefined,
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
      titleKey="stockWrite.transferTitle"
      noticeKey="stockWrite.transferNotice"
      submitKey="stockWrite.transferSubmit"
      doneKey="stockWrite.transferDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-item`}
        label={t("stockWrite.item")}
        value={item}
        required
        onChange={setItem}
      />
      <Field
        id={`${id}-qty`}
        label={t("stockWrite.qty")}
        value={qty}
        kind="number"
        required
        onChange={setQty}
      />
      <Field
        id={`${id}-source`}
        label={t("stockWrite.sourceWarehouse")}
        value={source}
        required
        onChange={setSource}
      />
      <Field
        id={`${id}-target`}
        label={t("stockWrite.targetWarehouse")}
        value={target}
        required
        onChange={setTarget}
      />
      <Field
        id={`${id}-batch`}
        label={t("stockWrite.batch")}
        value={batch}
        onChange={setBatch}
      />
    </FormShell>
  );
}

export function AdjustStockForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [reason, setReason] = useState("");
  const [batch, setBatch] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (get("write.stock.adjust") !== "enabled") return null;

  async function submit() {
    if (saving) return;
    const amount = Number(qty);
    if (
      !item.trim() ||
      !warehouse.trim() ||
      !reason.trim() ||
      !Number.isFinite(amount) ||
      amount < 0
    ) {
      setError(t("stockWrite.invalid"));
      return;
    }
    setError(null);
    setDone(false);
    setSaving(true);
    try {
      await adjustNativeStock({
        operation_key: newOperationKey(),
        item_code: item.trim(),
        qty: amount,
        warehouse: warehouse.trim(),
        reason: reason.trim(),
        batch_id: batch.trim() || undefined,
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
      titleKey="stockWrite.adjustTitle"
      noticeKey="stockWrite.adjustNotice"
      submitKey="stockWrite.adjustSubmit"
      doneKey="stockWrite.adjustDone"
      onSubmit={submit}
      done={done}
      error={error}
      saving={saving}
    >
      <Field
        id={`${id}-item`}
        label={t("stockWrite.item")}
        value={item}
        required
        onChange={setItem}
      />
      <Field
        id={`${id}-qty`}
        label={t("stockWrite.countedQty")}
        value={qty}
        kind="number"
        required
        onChange={setQty}
      />
      <Field
        id={`${id}-warehouse`}
        label={t("stockWrite.warehouse")}
        value={warehouse}
        required
        onChange={setWarehouse}
      />
      <Field
        id={`${id}-reason`}
        label={t("stockWrite.reason")}
        value={reason}
        maxLength={280}
        required
        onChange={setReason}
      />
      <Field
        id={`${id}-batch`}
        label={t("stockWrite.batch")}
        value={batch}
        onChange={setBatch}
      />
    </FormShell>
  );
}
