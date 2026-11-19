import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../design/components";
import { useCapabilities } from "../shell/CapabilityProvider";
import {
  receivePurchaseOrder,
  submitPurchaseOrder,
  type NativePurchaseWriteError,
} from "../../transport/native/purchase-write";
import {
  PurchaseDraftInFlightError,
  PurchaseDraftRejectedError,
  draftPurchaseOrder,
  findPurchaseDrafts,
  viewPurchaseOrder,
  type PurchaseDraftRecord,
} from "../../transport/native/purchase-draft";
import { nativeTransportEnabled } from "../../transport/native/flag";

/**
 * PHA-07 / PHA-05 purchasing server writes.
 *
 * Each form renders only when bootstrap advertises its own capability, so the
 * demo surfaces keep their sample state until the deployment gate opens. The
 * forms send a supplier, one itemized line (item, quantity, rate) or a
 * purchase-order reference with a quantity; every decision — the currency and
 * company, over-receipt, the PO pending balance, batch validity and the status
 * transition — stays server-side. The draft lane is additive: one request, no
 * acknowledgement, no browser lock and no retry, and the returned native id is
 * shown so an uncertain outcome can be reconciled against purchase records.
 * Creating a draft never submits it.
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

/**
 * A deterministic refusal is a known failure with its own copy; anything else
 * leaves the outcome unknown and points the reader at the records instead of
 * offering a repeat.
 */
function draftFailureKey(error: unknown): string | null {
  if (error instanceof PurchaseDraftInFlightError) return null;
  if (error instanceof PurchaseDraftRejectedError)
    return `purchaseWrite.${error.kind}`;
  return "purchaseWrite.draftUncertain";
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
  doneId?: string | null;
  error: string | null;
  saving: boolean;
  submitDisabled?: boolean;
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
  doneId = null,
  error,
  saving,
  submitDisabled = false,
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
          <Button type="submit" disabled={saving || submitDisabled}>
            {saving ? t("purchaseWrite.saving") : t(submitKey)}
          </Button>
        </div>
      </form>
      {done ? (
        <p role="status" className="ops-note">
          {doneId ? t(doneKey, { id: doneId }) : t(doneKey)}
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
  const { status, session } = useSession();
  const id = useId();
  const [supplier, setSupplier] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [rate, setRate] = useState("");
  const [saving, setSaving] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lookupId, setLookupId] = useState("");
  const [records, setRecords] = useState<PurchaseDraftRecord[] | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [uncertain, setUncertain] = useState(false);

  const canWrite = get("write.purchase_order.draft") === "enabled";
  const canInspect = nativeTransportEnabled() && status === "authenticated" &&
    session?.user.roles.some((role) => role === "PHA" || role === "MGR");

  if (!canWrite && !canInspect) return null;

  async function submit() {
    if (saving || uncertain) return;
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
    setCreatedId(null);
    setSaving(true);
    try {
      const created = await draftPurchaseOrder({
        operation_key: newOperationKey(),
        supplier: supplier.trim(),
        items: [{ item_code: item.trim(), qty: amount, rate: price }],
      });
      setCreatedId(created.purchase_order_id);
      setLookupId(created.purchase_order_id);
      setQty("");
      setRate("");
    } catch (failure) {
      const key = draftFailureKey(failure);
      if (key) setError(t(key));
      if (key === "purchaseWrite.draftUncertain") setUncertain(true);
    } finally {
      setSaving(false);
    }
  }

  async function lookup(kind: "drafts" | "view") {
    const value = (kind === "drafts" ? supplier : lookupId).trim();
    if (!value) {
      setLookupError(t(kind === "drafts" ? "purchaseWrite.lookupSupplierRequired" : "purchaseWrite.lookupIdRequired"));
      setRecords(null);
      return;
    }
    setLooking(true);
    setLookupError(null);
    setRecords(null);
    try {
      setRecords(kind === "drafts" ? await findPurchaseDrafts(value) : [await viewPurchaseOrder(value)]);
    } catch {
      setLookupError(t("purchaseWrite.lookupFailed"));
    } finally {
      setLooking(false);
    }
  }

  return (
    <>
    {canWrite ? <FormShell
      titleKey="purchaseWrite.draftTitle"
      noticeKey="purchaseWrite.draftNotice"
      submitKey="purchaseWrite.draftSubmit"
      doneKey="purchaseWrite.draftDone"
      onSubmit={submit}
      done={createdId !== null}
      doneId={createdId}
      error={error}
      saving={saving}
      submitDisabled={uncertain}
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
    </FormShell> : null}
    {canInspect ? (
      <section className="admin-write" aria-label={t("purchaseWrite.lookupTitle")}>
        <h3>{t("purchaseWrite.lookupTitle")}</h3>
        <p className="pc-note">{t("purchaseWrite.lookupNotice")}</p>
        {!canWrite ? <Field id={`${id}-lookup-supplier`} label={t("purchaseWrite.supplier")} value={supplier} onChange={setSupplier} /> : null}
        <Button type="button" disabled={looking} onClick={() => void lookup("drafts")}>
          {t("purchaseWrite.findDrafts")}
        </Button>
        <Field id={`${id}-lookup`} label={t("purchaseWrite.lookupId")} value={lookupId} onChange={setLookupId} />
        <Button type="button" disabled={looking} onClick={() => void lookup("view")}>
          {t("purchaseWrite.viewOrder")}
        </Button>
        {looking ? <p role="status">{t("purchaseWrite.lookupLoading")}</p> : null}
        {lookupError ? <p role="alert" className="pc-error">{lookupError}</p> : null}
        {records !== null ? (
          <div role="status">
            {records.length === 0 ? <p>{t("purchaseWrite.noDrafts")}</p> : (
              <ul>{records.map((record) => (
                <li key={record.purchase_order_id}>
                  {record.purchase_order_id} — {t("purchaseWrite.lookupStatus")}: {record.status ?? "—"}; {t("purchaseWrite.lookupDocstatus")}: {record.docstatus}
                </li>
              ))}</ul>
            )}
          </div>
        ) : null}
      </section>
    ) : null}
    </>
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
