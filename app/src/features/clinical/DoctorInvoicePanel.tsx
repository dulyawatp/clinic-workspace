import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Button, Card } from "../../design/components";
import "./clinical.css";

export type DoctorInvoiceDiscount = { kind: "none" } | { kind: "fixed" | "percent"; value: string };
export type DoctorInvoiceReviewInput = { discount: DoctorInvoiceDiscount };
export type DoctorInvoiceLine = { id: string; description: string; quantity: number; amount?: number };
export type DoctorInvoicePreparation = {
  currency: string;
  lines: readonly DoctorInvoiceLine[];
  taxTemplate?: string;
  applyDiscountOn?: "Grand Total" | "Net Total";
};
export type DoctorInvoiceReview = DoctorInvoicePreparation & {
  taxes: readonly { label: string; amount: number }[];
  subtotal: number;
  discount: number;
  taxTotal: number;
  grandTotal: number;
  roundedTotal: number;
};
export type DoctorInvoiceDraft = {
  id: string;
  /** Internal React route; omit unless native permissions allow opening the document. */
  viewHref?: string;
};
export type DoctorIssuedInvoice = DoctorInvoiceDraft & {
  currency: string;
  grandTotal: number;
  roundedTotal: number;
};

type Issued = { status: "issued"; invoice: DoctorIssuedInvoice };
/** Definite persisted draft, not an in-flight or unconfirmed native submit. */
type Draft = { status: "draft"; draft: DoctorInvoiceDraft };
type Unknown = { status: "unknown"; draft?: DoctorInvoiceDraft };
export type DoctorInvoiceSubmitResult = Issued | Draft | Unknown | { status: "rejected" };
export type DoctorInvoiceCheckResult = Issued | Draft | Unknown | { status: "confirmed_absent" };
export type DoctorInvoicePanelProps = {
  /** UI identity only. Keep this instance mounted while reconciling the same invoice attempt. */
  sourceKey: string;
  preparation: DoctorInvoicePreparation;
  /** Bind all three callbacks to the same native invoice preparation. Review performs no write. */
  reviewInvoice: (input: DoctorInvoiceReviewInput) => Promise<DoctorInvoiceReview>;
  /** No adapter retry. Only use `rejected` for definite noncreation; return the known draft ID if insert succeeded. */
  submitInvoice: (input: DoctorInvoiceReviewInput) => Promise<DoctorInvoiceSubmitResult>;
  /** Read native status independently. Not-found alone is not `confirmed_absent` if creation might still commit. */
  checkInvoiceStatus: (input: { draftId?: string }) => Promise<DoctorInvoiceCheckResult>;
};

const copy = {
  en: {
    title: "Sales Invoice", lines: "Invoice lines", quantity: "Quantity", taxTemplate: "Tax Template",
    discount: "Additional Discount", basis: "Apply Additional Discount On",
    net: "Net Total",
    none: "None", fixed: "Fixed amount", percent: "Percentage",
    amount: "Discount Amount", percentage: "Discount Percentage",
    invalid: "Enter a number.",
    review: "Review total", reviewing: "Reviewing total…", reviewed: "Reviewed total",
    reviewFailed: "Frappe could not review the total.",
    submit: "Submit Invoice", submitting: "Submitting invoice…",
    confirm: "Confirm these totals before submitting the Sales Invoice.",
    unknown: "Invoice status is not confirmed. Check the status before submitting again.",
    check: "Check invoice status", checking: "Checking invoice status…",
    absent: "No invoice was found. Review the total again before submitting.",
    rejected: "Frappe did not create an invoice.",
    draft: "Draft invoice saved", draftHint: "This invoice has not been submitted.",
    issued: "Invoice issued", view: "View invoice",
    subtotal: "Subtotal", taxes: "Taxes", grand: "Grand Total", rounded: "Rounded Total",
  },
  th: {
    title: "ใบแจ้งหนี้การขาย", lines: "รายการในใบแจ้งหนี้", quantity: "จำนวน", taxTemplate: "แม่แบบภาษี",
    discount: "ส่วนลดเพิ่มเติม", basis: "ใช้ส่วนลดเพิ่มเติมกับ",
    net: "ยอดรวมสุทธิ",
    none: "ไม่มี", fixed: "จำนวนเงินคงที่", percent: "เปอร์เซ็นต์",
    amount: "จำนวนเงินส่วนลด", percentage: "เปอร์เซ็นต์ส่วนลด",
    invalid: "กรอกตัวเลข",
    review: "ตรวจสอบยอดรวม", reviewing: "กำลังตรวจสอบยอดรวม…", reviewed: "ยอดรวมที่ตรวจสอบแล้ว",
    reviewFailed: "Frappe ไม่สามารถตรวจสอบยอดรวมได้",
    submit: "ยืนยันใบแจ้งหนี้", submitting: "กำลังยืนยันใบแจ้งหนี้…",
    confirm: "ตรวจสอบยอดเหล่านี้ก่อนยืนยันใบแจ้งหนี้การขาย",
    unknown: "ยังยืนยันสถานะใบแจ้งหนี้ไม่ได้ โปรดตรวจสอบสถานะก่อนยืนยันอีกครั้ง",
    check: "ตรวจสอบสถานะใบแจ้งหนี้", checking: "กำลังตรวจสอบสถานะใบแจ้งหนี้…",
    absent: "ไม่พบใบแจ้งหนี้ โปรดตรวจสอบยอดรวมอีกครั้งก่อนยืนยัน",
    rejected: "Frappe ไม่ได้สร้างใบแจ้งหนี้",
    draft: "บันทึกใบแจ้งหนี้ฉบับร่างแล้ว", draftHint: "ยังไม่ได้ยืนยันใบแจ้งหนี้นี้",
    issued: "ออกใบแจ้งหนี้แล้ว", view: "ดูใบแจ้งหนี้",
    subtotal: "ยอดรวมก่อนหักส่วนลด", taxes: "ภาษี", grand: "ยอดรวมทั้งสิ้น", rounded: "ยอดรวมหลังปัดเศษ",
  },
};

type Phase =
  | { status: "editing"; notice?: "reviewFailed" | "absent" | "rejected" }
  | { status: "reviewing" }
  | { status: "checking"; draft?: DoctorInvoiceDraft }
  | { status: "reviewed" | "submitting"; review: DoctorInvoiceReview; input: DoctorInvoiceReviewInput }
  | Unknown | Draft | Issued;

type Operation = { kind: "review" | "submit" | "check" };
const grid = { display: "grid", minWidth: 0, gap: "0.75rem" } as const;
const wrap = { display: "flex", flexWrap: "wrap", alignItems: "baseline", justifyContent: "space-between", gap: "0.25rem 1rem" } as const;
const control = { width: "100%", minWidth: 0 } as const;

function Totals({ rows, highlight = true }: { rows: readonly [label: string, amount: string][]; highlight?: boolean }) {
  return <dl style={{ ...grid, margin: 0, fontVariantNumeric: "tabular-nums" }}>
    {rows.map(([label, amount], index) => {
      const last = highlight && index === rows.length - 1;
      return <div key={`${label}-${index}`} style={{ ...wrap, ...(last && { borderTop: "1px solid var(--border)", paddingTop: "0.75rem", fontWeight: 700 }) }}>
        <dt>{label}</dt><dd style={{ margin: 0 }}>{amount}</dd>
      </div>;
    })}
  </dl>;
}

export function DoctorInvoicePanel(props: DoctorInvoicePanelProps) {
  return <InvoiceEditor key={props.sourceKey} {...props} />;
}

function InvoiceEditor({ preparation, reviewInvoice, submitInvoice, checkInvoiceStatus }: DoctorInvoicePanelProps) {
  const { i18n } = useTranslation();
  const thai = i18n.language === "th";
  const text = copy[thai ? "th" : "en"];
  const id = useId();
  const feedback = useRef<HTMLElement | null>(null);
  const focusTarget = (node: HTMLElement | null) => { feedback.current = node; };
  const operation = useRef<Operation | null>(null);
  const [phase, setPhase] = useState<Phase>({ status: "editing" });
  const [discountKind, setDiscountKind] = useState<DoctorInvoiceDiscount["kind"]>("none");
  const [value, setValue] = useState("");
  const valid = discountKind === "none" || (value !== "" && Number.isFinite(Number(value)));
  // Updated native inputs invalidate previews but never clear a write outcome or lock.
  const preparationKey = JSON.stringify(preparation);
  const previousPreparation = useRef(preparationKey);
  useLayoutEffect(() => {
    if (previousPreparation.current !== preparationKey) {
      previousPreparation.current = preparationKey;
      setPhase((current) => current.status === "reviewed" || current.status === "reviewing" ? { status: "editing" } : current);
    }
    return () => {
      if (operation.current?.kind === "review") operation.current = null;
    };
  }, [preparationKey]);

  const notice = phase.status === "editing" ? phase.notice : undefined;
  useEffect(() => {
    if (phase.status === "reviewed" || phase.status === "issued" || phase.status === "unknown" || phase.status === "draft" || notice) {
      feedback.current?.focus();
    }
  }, [phase.status, notice]);

  function edit(change: () => void) {
    if ((phase.status !== "editing" && phase.status !== "reviewing" && phase.status !== "reviewed") ||
      (operation.current && operation.current.kind !== "review")) return;
    operation.current = null;
    setPhase({ status: "editing" });
    change();
  }

  async function run(kind: Operation["kind"], work: () => Promise<Phase>, fallback: Phase) {
    const request = { kind };
    operation.current = request;
    try {
      const result = await work();
      if (operation.current === request) setPhase(result);
    } catch {
      if (operation.current === request) setPhase(fallback);
    } finally {
      if (operation.current === request) operation.current = null;
    }
  }

  function review() {
    if (phase.status !== "editing" || operation.current || !valid) return;
    const input: DoctorInvoiceReviewInput = {
      discount: discountKind === "none" ? { kind: "none" } : { kind: discountKind, value },
    };
    setPhase({ status: "reviewing" });
    void run("review", async () => ({ status: "reviewed", input, review: await reviewInvoice(input) }),
      { status: "editing", notice: "reviewFailed" });
  }

  function submit() {
    if (phase.status !== "reviewed" || operation.current) return;
    const { review: reviewed, input } = phase;
    setPhase({ status: "submitting", review: reviewed, input });
    void run("submit", async () => {
      const result = await submitInvoice(input);
      return result.status === "rejected" ? { status: "editing", notice: "rejected" } : result;
    }, { status: "unknown" });
  }

  function check() {
    if (phase.status !== "unknown" || operation.current) return;
    const pending = phase;
    setPhase({ ...pending, status: "checking" });
    void run("check", async () => {
      const result = await checkInvoiceStatus(pending.draft ? { draftId: pending.draft.id } : {});
      if (result.status === "confirmed_absent") {
        return pending.draft ? pending : { status: "editing", notice: "absent" };
      }
      if (result.status === "unknown") return result.draft ? result : pending;
      return result;
    }, pending);
  }

  const card = (children: ReactNode) => <Card title={text.title}>
    <div style={{ ...grid, overflowWrap: "anywhere" }}>{children}</div>
  </Card>;
  const format = (currency: string) => new Intl.NumberFormat(thai ? "th-TH" : "en-GB", {
    style: "currency", currency, maximumFractionDigits: 12,
  }).format;
  if (phase.status === "issued" || phase.status === "draft") {
    const invoice = phase.status === "issued" ? phase.invoice : null;
    const doc = phase.status === "issued" ? phase.invoice : phase.draft;
    const money = invoice ? format(invoice.currency) : null;
    return card(<>
      <h3 tabIndex={-1} ref={focusTarget} style={{ margin: 0 }}>{text[phase.status]}</h3>
      <p style={{ margin: 0 }}><bdi>{doc.id}</bdi></p>
      {invoice && money ? <Totals rows={[[text.grand, money(invoice.grandTotal)], [text.rounded, money(invoice.roundedTotal)]]} /> :
        <p className="pc-note" style={{ margin: 0 }}>{text.draftHint}</p>}
      {doc.viewHref && <div><Link to={doc.viewHref} className="pc-button pc-button--secondary">{text.view}</Link></div>}
    </>);
  }

  const reviewed = phase.status === "reviewed" || phase.status === "submitting" ? phase.review : null;
  const uncertain = phase.status === "unknown" || phase.status === "checking";
  const locked = uncertain || phase.status === "submitting";
  const displayed = reviewed ?? preparation;
  const money = format(displayed.currency);
  const quantity = new Intl.NumberFormat(thai ? "th-TH" : "en-GB", { maximumFractionDigits: 12 });
  const needsValue = discountKind !== "none";

  return card(<>
    <section aria-labelledby={`${id}-lines`} style={grid}>
      <h3 id={`${id}-lines`} style={{ margin: 0 }}>{text.lines}</h3>
      <ul role="list" style={{ ...grid, margin: 0, padding: 0, listStyle: "none" }}>
        {displayed.lines.map((line) => <li key={line.id} style={{ ...wrap, borderBottom: "1px solid var(--border)", paddingBottom: "0.5rem" }}>
          <strong>{line.description}</strong>
          <span>{text.quantity}: {quantity.format(line.quantity)}{line.amount === undefined ? "" : ` · ${money(line.amount)}`}</span>
        </li>)}
      </ul>
      {displayed.taxTemplate && <dl style={{ ...wrap, margin: 0 }}>
        <dt>{text.taxTemplate}</dt><dd style={{ margin: 0 }}>{displayed.taxTemplate}</dd>
      </dl>}
    </section>
    <fieldset disabled={locked} style={{ ...grid, border: 0, margin: 0, padding: 0, maxWidth: "32rem" }}>
      <div className="pc-field">
        <label className="pc-field__label" htmlFor={`${id}-discount`}>{text.discount}</label>
        <select id={`${id}-discount`} value={discountKind} style={control}
          aria-describedby={displayed.applyDiscountOn ? `${id}-basis` : undefined} onChange={(event) => edit(() => {
          const next = event.currentTarget.value;
          setDiscountKind(next === "fixed" || next === "percent" ? next : "none");
          setValue("");
        })}>
          <option value="none">{text.none}</option>
          <option value="fixed">{text.fixed}</option>
          <option value="percent">{text.percent}</option>
        </select>
        {displayed.applyDiscountOn && <p id={`${id}-basis`} className="pc-note" style={{ margin: 0 }}>
          {`${text.basis}: ${displayed.applyDiscountOn === "Net Total" ? text.net : text.grand}`}
        </p>}
      </div>
      {needsValue && <div className="pc-field">
        <label className="pc-field__label" htmlFor={`${id}-value`}>{discountKind === "fixed" ? text.amount : text.percentage}</label>
        <input id={`${id}-value`} type="number" inputMode="decimal" step="any" style={control} value={value}
          aria-invalid={value !== "" && !valid}
          aria-describedby={value !== "" && !valid ? `${id}-invalid` : undefined}
          onChange={(event) => edit(() => setValue(event.currentTarget.value))} />
        {value !== "" && !valid && <p id={`${id}-invalid`} className="pc-note" style={{ margin: 0 }}>{text.invalid}</p>}
      </div>}
    </fieldset>
    {notice && <p role="status" tabIndex={-1} ref={focusTarget} style={{ margin: 0 }}>{text[notice]}</p>}
    {(phase.status === "editing" || phase.status === "reviewing") && <div>
      <Button variant="primary" onClick={review} disabled={phase.status === "reviewing" || !valid}>
        {phase.status === "reviewing" ? text.reviewing : text.review}
      </Button>
    </div>}
    {reviewed && <section aria-labelledby={`${id}-reviewed`} className="visit-prescription-draft" style={grid}>
      <h3 id={`${id}-reviewed`} tabIndex={-1} ref={focusTarget} style={{ margin: 0 }}>{text.reviewed}</h3>
      {reviewed.taxes.length > 0 && <Totals highlight={false} rows={reviewed.taxes.map(({ label, amount }) => [label, money(amount)])} />}
      <Totals rows={[
        [text.subtotal, money(reviewed.subtotal)], [text.discount, money(reviewed.discount)],
        [text.taxes, money(reviewed.taxTotal)], [text.grand, money(reviewed.grandTotal)], [text.rounded, money(reviewed.roundedTotal)],
      ]} />
      <p id={`${id}-confirm`} className="pc-note" style={{ margin: 0 }}>{text.confirm}</p>
      <div><Button variant="primary" onClick={submit} aria-describedby={`${id}-confirm`} disabled={phase.status === "submitting"}>
        {phase.status === "submitting" ? text.submitting : text.submit}
      </Button></div>
    </section>}
    {uncertain && <>
      <p role="status" tabIndex={-1} ref={focusTarget} style={{ margin: 0 }}>{text.unknown}</p>
      {phase.draft && <p style={{ margin: 0 }}><bdi>{phase.draft.id}</bdi></p>}
      <div><Button variant="primary" onClick={check} disabled={phase.status === "checking"}>
        {phase.status === "checking" ? text.checking : text.check}
      </Button></div>
    </>}
  </>);
}
