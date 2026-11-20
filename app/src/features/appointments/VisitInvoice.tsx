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
  type VisitInvoiceVatMode,
} from "../../transport/native/visit-invoice";
import { Button } from "../../ui/Button";

type Review = { input: VisitInvoiceInput; preview: VisitInvoicePreview };
type Operation = "preview" | "submit" | "lookup";

export function VisitInvoice({
  appointmentId,
  canSubmit,
  canDiscount,
  canChooseNoVat,
}: {
  appointmentId: string;
  canSubmit: boolean;
  canDiscount: boolean;
  canChooseNoVat: boolean;
}) {
  const { t } = useTranslation();
  const heading = useId();
  const pending = useRef(false);
  const previewRevision = useRef(0);
  const [busy, setBusy] = useState<Operation | null>(null);
  const [vatMode, setVatMode] = useState<VisitInvoiceVatMode>("vat_7");
  const [mode, setMode] = useState<"none" | VisitInvoiceDiscount["mode"]>("none");
  const [amount, setAmount] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [invoice, setInvoice] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupUnavailable, setLookupUnavailable] = useState(false);

  const canEdit = canSubmit && !uncertain && invoice === null;
  const activeVatMode = canChooseNoVat ? vatMode : "vat_7";
  const activeReview = canEdit && review && review.input.vat_mode === activeVatMode &&
    (canDiscount || !review.input.discount) ? review : null;
  const activeMode = canDiscount ? mode : "none";
  const inputsLocked = busy === "submit" || busy === "lookup";

  function invalidatePreview() {
    previewRevision.current += 1;
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
    const revision = previewRevision.current;
    const input: VisitInvoiceInput = { appointment_id: appointmentId, vat_mode: activeVatMode };
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
        const preview = await previewVisitInvoice(input);
        if (revision === previewRevision.current) setReview({ input, preview });
      } catch (failure) {
        if (revision === previewRevision.current) {
          setError(failure instanceof VisitInvoiceError && failure.code === "validation"
            ? "invalid" : "previewUnavailable");
        }
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
      {canEdit && (
        <form className="appointment-write-form" onSubmit={requestPreview} noValidate>
          {!canChooseNoVat && <p className="appointment-write-note">{t("visitInvoice.vat7")}</p>}
          {(canChooseNoVat || canDiscount) && (
            <div className="appointment-form">
              {canChooseNoVat && (
                <label>
                  {t("visitInvoice.vat")}
                  <select value={activeVatMode} disabled={inputsLocked} onChange={(event) => {
                    setVatMode(event.target.value === "no_vat" ? "no_vat" : "vat_7");
                    invalidatePreview();
                  }}>
                    <option value="vat_7">{t("visitInvoice.vat7")}</option>
                    <option value="no_vat">{t("visitInvoice.noVat")}</option>
                  </select>
                </label>
              )}
              {canDiscount && (
                <label>
                  {t("visitInvoice.discount")}
                  <select value={mode} disabled={inputsLocked} onChange={(event) => {
                    setMode(event.target.value as typeof mode);
                    setAmount("");
                    invalidatePreview();
                  }}>
                    <option value="none">{t("visitInvoice.none")}</option>
                    <option value="amount">{t("visitInvoice.amount")}</option>
                    <option value="percent">{t("visitInvoice.percent")}</option>
                  </select>
                </label>
              )}
              {activeMode !== "none" && (
                <label>
                  {t(activeMode === "amount" ? "visitInvoice.amount" : "visitInvoice.percent")}
                  <input
                    inputMode="decimal"
                    maxLength={10}
                    value={amount}
                    disabled={inputsLocked}
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
            {(["gross", "discount", "net", "vat", "total"] as const).map((field) => (
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
