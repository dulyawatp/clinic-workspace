import { useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  previewVisitInvoice,
  submitVisitInvoice,
  viewVisitInvoice,
  VisitInvoiceError,
  type VisitInvoiceDiscount,
  type VisitInvoiceInput,
  type VisitInvoicePreview,
} from "../../transport/native/visit-invoice";
import { Button } from "../../ui/Button";

type Review = { input: VisitInvoiceInput; preview: VisitInvoicePreview };
type Operation = "preview" | "submit" | "lookup";

export function VisitInvoice({
  appointmentId,
  canSubmit,
  canDiscount,
}: {
  appointmentId: string;
  canSubmit: boolean;
  canDiscount: boolean;
}) {
  const { t } = useTranslation();
  const heading = useId();
  const pending = useRef(false);
  const [busy, setBusy] = useState<Operation | null>(null);
  const [mode, setMode] = useState<"none" | VisitInvoiceDiscount["mode"]>("none");
  const [amount, setAmount] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [invoice, setInvoice] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupUnavailable, setLookupUnavailable] = useState(false);

  const canEdit = canSubmit && !uncertain && invoice === null;
  const activeReview = canEdit && review && (canDiscount || !review.input.discount) ? review : null;
  const activeMode = canDiscount ? mode : "none";

  function invalidatePreview() {
    setReview(null);
    setError(null);
  }

  async function run(operation: Operation, action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(operation);
    try {
      await action();
    } finally {
      pending.current = false;
      setBusy(null);
    }
  }

  function requestPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canEdit || pending.current) return;
    invalidatePreview();
    const input: VisitInvoiceInput = { appointment_id: appointmentId };
    if (activeMode !== "none") {
      if (!/^\d{1,7}(?:\.\d{1,2})?$/.test(amount) ||
          Number(amount) <= 0 || Number(amount) > (activeMode === "percent" ? 100 : 1_000_000)) {
        setError("invalidDiscount");
        return;
      }
      input.discount = { mode: activeMode, value: amount };
    }
    void run("preview", async () => {
      try {
        setReview({ input, preview: await previewVisitInvoice(input) });
      } catch (failure) {
        setError(failure instanceof VisitInvoiceError && failure.code === "validation"
          ? "invalid" : "previewUnavailable");
      }
    });
  }

  function confirm() {
    if (!activeReview || pending.current) return;
    setError(null);
    void run("submit", async () => {
      try {
        const result = await submitVisitInvoice({
          ...activeReview.input,
          preview_token: activeReview.preview.preview_token,
        });
        setInvoice(result.invoice_id);
      } catch (failure) {
        if (failure instanceof VisitInvoiceError && failure.code !== "uncertain") {
          setError(failure.code === "validation" ? "invalid" : "submitUnavailable");
        } else {
          setUncertain(true);
        }
      } finally {
        setReview(null);
      }
    });
  }

  function lookup() {
    void run("lookup", async () => {
      setLookupUnavailable(false);
      try {
        setInvoice((await viewVisitInvoice(appointmentId)).invoice_id);
        setReview(null);
      } catch {
        setLookupUnavailable(true);
      }
    });
  }

  return (
    <section className="appointment-card appointment-write-card visit-invoice" aria-labelledby={heading}>
      <h2 id={heading}>{t("visitInvoice.title")}</h2>
      {canEdit && (canDiscount || !activeReview) && (
        <form className="appointment-write-form" onSubmit={requestPreview} noValidate>
          {canDiscount && (
            <div className="appointment-form">
              <label>
                {t("visitInvoice.discount")}
                <select value={mode} disabled={busy !== null} onChange={(event) => {
                  setMode(event.target.value as typeof mode);
                  setAmount("");
                  invalidatePreview();
                }}>
                  <option value="none">{t("visitInvoice.none")}</option>
                  <option value="amount">{t("visitInvoice.amount")}</option>
                  <option value="percent">{t("visitInvoice.percent")}</option>
                </select>
              </label>
              {activeMode !== "none" && (
                <label>
                  {t(activeMode === "amount" ? "visitInvoice.amount" : "visitInvoice.percent")}
                  <input
                    inputMode="decimal"
                    maxLength={10}
                    value={amount}
                    disabled={busy !== null}
                    aria-invalid={error === "invalidDiscount"}
                    onChange={(event) => {
                      setAmount(event.target.value);
                      invalidatePreview();
                    }}
                  />
                </label>
              )}
            </div>
          )}
          {error && <p className="appointment-write-alert" role="alert">{t(`visitInvoice.${error}`)}</p>}
          {!activeReview && (
            <div className="appointment-write-actions">
              <Button type="submit" variant="secondary" disabled={busy !== null}>
                {t("visitInvoice.preview")}
              </Button>
            </div>
          )}
        </form>
      )}
      {activeReview && (
        <div className="visit-invoice-review">
          <dl className="appointment-detail-list">
            {(["gross", "discount", "total"] as const).map((field) => (
              <div className={field === "total" ? "visit-invoice-total" : undefined} key={field}>
                <dt>{t(`visitInvoice.${field}`)}</dt>
                <dd>{activeReview.preview.currency} {activeReview.preview[field]}</dd>
              </div>
            ))}
          </dl>
          <Button onClick={confirm} disabled={busy !== null}>{t("visitInvoice.confirm")}</Button>
        </div>
      )}
      {invoice !== null ? (
        <p className="appointment-write-success" role="status">{t("visitInvoice.posted", { invoice })}</p>
      ) : (
        <>
          {uncertain && <p className="appointment-write-alert" role="alert">{t("visitInvoice.uncertain")}</p>}
          {!canSubmit && !uncertain && <p className="appointment-write-note">{t("visitInvoice.readOnly")}</p>}
          {lookupUnavailable && <p className="appointment-write-note" role="status">{t("visitInvoice.lookupUnavailable")}</p>}
          <div className="appointment-write-actions">
            <Button variant="secondary" onClick={lookup} disabled={busy !== null}>{t("visitInvoice.lookup")}</Button>
            {busy && <span role="status">{t(`visitInvoice.${busy}Busy`)}</span>}
          </div>
        </>
      )}
    </section>
  );
}
