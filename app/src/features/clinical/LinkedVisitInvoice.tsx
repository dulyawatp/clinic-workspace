import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Card } from "../../design/components";
import {
  getHealthcareInvoiceContext,
  prepareHealthcareInvoice,
  readHealthcareInvoice,
} from "../../transport/native/native-healthcare-invoice";
import { NativeInvoiceHttpError } from "../../transport/native/native-invoice";
import { createNativeInvoiceActions } from "../../transport/native/native-invoice-actions";
import { DoctorInvoicePanel, type DoctorInvoicePanelProps } from "./DoctorInvoicePanel";

type Context = Awaited<ReturnType<typeof getHealthcareInvoiceContext>>;
type Actions = Omit<DoctorInvoicePanelProps, "sourceKey">;
type Failure = "denied" | "unavailable";
type Stage =
  | { status: "closed" | "loading" | "empty" | Failure }
  | { status: "select" | "preparing"; context: Context; failure?: Failure }
  | { status: "prepared"; context: Context; actions: Actions };

const copy = {
  en: {
    title: "Visit billing", open: "Visit invoice", loading: "Loading billable services…",
    services: "Patient billable services",
    scope: "Marley lists billable services for this patient, not just this visit. Check each reference and select only services to include in this invoice.",
    quantity: "Quantity", unmapped: "No invoice item linked", prepare: "Prepare invoice", preparing: "Preparing invoice…",
    prepared: "Invoice prepared. Review the total before submitting.",
    change: "Change selected services", retry: "Reload billable services",
    empty: "No billable services were returned for this patient.",
    denied: "Marley did not allow access to these billable services.",
    unavailable: "Billable services are unavailable right now.",
    prepareDenied: "Marley did not allow this invoice to be prepared.",
    prepareUnavailable: "The invoice could not be prepared. Try again.",
  },
  th: {
    title: "การเรียกเก็บเงินสำหรับการเข้ารับบริการ", open: "ใบแจ้งหนี้สำหรับการเข้ารับบริการ",
    loading: "กำลังโหลดบริการที่เรียกเก็บเงินได้…", services: "บริการที่เรียกเก็บเงินได้ของผู้ป่วย",
    scope: "Marley แสดงบริการที่เรียกเก็บเงินได้ทั้งหมดของผู้ป่วย ไม่เฉพาะการเข้ารับบริการครั้งนี้ ตรวจสอบเลขอ้างอิงและเลือกเฉพาะรายการที่จะใส่ในใบแจ้งหนี้นี้",
    quantity: "จำนวน", unmapped: "ยังไม่ได้เชื่อมกับรายการในใบแจ้งหนี้", prepare: "เตรียมใบแจ้งหนี้", preparing: "กำลังเตรียมใบแจ้งหนี้…",
    prepared: "เตรียมใบแจ้งหนี้แล้ว ตรวจสอบยอดรวมก่อนยืนยัน",
    change: "เปลี่ยนรายการบริการที่เลือก", retry: "โหลดบริการที่เรียกเก็บเงินได้อีกครั้ง",
    empty: "ไม่พบรายการบริการที่เรียกเก็บเงินได้สำหรับผู้ป่วยรายนี้",
    denied: "Marley ไม่อนุญาตให้เข้าถึงรายการบริการที่เรียกเก็บเงินได้",
    unavailable: "ไม่สามารถโหลดรายการบริการที่เรียกเก็บเงินได้ในขณะนี้",
    prepareDenied: "Marley ไม่อนุญาตให้เตรียมใบแจ้งหนี้นี้",
    prepareUnavailable: "ไม่สามารถเตรียมใบแจ้งหนี้ได้ กรุณาลองอีกครั้ง",
  },
};

function failure(error: unknown): Failure {
  return error instanceof NativeInvoiceHttpError && (error.status === 401 || error.status === 403)
    ? "denied" : "unavailable";
}

export function LinkedVisitInvoice({ appointment }: { appointment: string }) {
  return <VisitInvoicePicker key={appointment} appointment={appointment} />;
}

function VisitInvoicePicker({ appointment }: { appointment: string }) {
  const { i18n } = useTranslation();
  const thai = i18n.language === "th";
  const text = copy[thai ? "th" : "en"];
  const quantity = new Intl.NumberFormat(thai ? "th-TH" : "en-GB", { maximumFractionDigits: 12 });
  const id = useId();
  const feedback = useRef<HTMLElement | null>(null);
  const focusTarget = (node: HTMLElement | null) => { feedback.current = node; };
  const request = useRef<AbortController | null>(null);
  const submitStarted = useRef(false);
  const [locked, setLocked] = useState(false);
  const [stage, setStage] = useState<Stage>({ status: "closed" });
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const issue = stage.status === "select" ? stage.failure : undefined;

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (stage.status !== "closed" && stage.status !== "loading" && stage.status !== "preparing") feedback.current?.focus();
  }, [stage.status, issue]);

  async function load() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setStage({ status: "loading" });
    try {
      const context = await getHealthcareInvoiceContext(appointment, controller.signal);
      if (!controller.signal.aborted) setStage(context.rows.length ? { status: "select", context } : { status: "empty" });
    } catch (error) {
      if (!controller.signal.aborted) setStage({ status: failure(error) });
    } finally {
      request.current = null;
    }
  }

  async function prepare(context: Context) {
    if (request.current || submitStarted.current || !selected.size) return;
    const controller = new AbortController();
    request.current = controller;
    const rows = context.rows.filter((_, index) => selected.has(index));
    setStage({ status: "preparing", context });
    try {
      const doc = await prepareHealthcareInvoice(context, rows, controller.signal);
      if (controller.signal.aborted) return;
      const actions = createNativeInvoiceActions(doc, readHealthcareInvoice);
      setStage({ status: "prepared", context, actions: {
        ...actions,
        submitInvoice(input) {
          submitStarted.current = true;
          setLocked(true);
          return actions.submitInvoice(input);
        },
      } });
    } catch (error) {
      if (!controller.signal.aborted) setStage({ status: "select", context, failure: failure(error) });
    } finally {
      request.current = null;
    }
  }

  function toggle(index: number, checked: boolean) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(index);
      else next.delete(index);
      return next;
    });
  }

  if (stage.status === "prepared") {
    return <div className="visit-preview-stack">
      <div className="visit-preview-heading">
        <p className="pc-note" tabIndex={-1} ref={focusTarget}>{text.prepared}</p>
        {!locked && <Button onClick={() => {
          if (!submitStarted.current) setStage({ status: "select", context: stage.context });
        }}>{text.change}</Button>}
      </div>
      <DoctorInvoicePanel sourceKey={appointment} {...stage.actions} />
    </div>;
  }

  return <Card title={text.title}>
    <div className="visit-preview-stack" style={{ overflowWrap: "anywhere" }}>
      {stage.status === "closed" && <div><Button onClick={() => void load()}>{text.open}</Button></div>}
      {stage.status === "loading" && <p role="status" className="pc-note">{text.loading}</p>}
      {(stage.status === "empty" || stage.status === "denied" || stage.status === "unavailable") && <>
        <p role="status" className="pc-note" tabIndex={-1} ref={focusTarget}>{text[stage.status]}</p>
        {stage.status === "unavailable" && <div><Button onClick={() => void load()}>{text.retry}</Button></div>}
      </>}
      {(stage.status === "select" || stage.status === "preparing") && <>
        <h3 id={`${id}-services`} tabIndex={-1} ref={issue ? undefined : focusTarget}>{text.services}</h3>
        <p id={`${id}-scope`} className="pc-note">{text.scope}</p>
        <fieldset className="visit-preview-stack" aria-labelledby={`${id}-services`} aria-describedby={`${id}-scope`}
          disabled={stage.status === "preparing"} style={{ margin: 0, padding: 0, border: 0 }}>
          {stage.context.rows.map((row, index) => <div key={index} className="visit-prescription-row">
            <label style={{ display: "flex", gap: "0.625rem", alignItems: "start" }}>
              <input type="checkbox" disabled={!row.service?.trim()} checked={selected.has(index)} aria-describedby={`${id}-reference-${index}`}
                onChange={(event) => toggle(index, event.currentTarget.checked)} />
              <strong>{row.service?.trim() ? row.service : text.unmapped}</strong>
            </label>
            <p id={`${id}-reference-${index}`} className="pc-note">
              <bdi>{`${row.reference_type} · ${row.reference_name}`}</bdi>
              {row.qty != null && <span>{` · ${text.quantity}: ${quantity.format(Number(row.qty))}`}</span>}
            </p>
          </div>)}
        </fieldset>
        {issue && <p role="status" className="pc-note" tabIndex={-1} ref={focusTarget}>
          {issue === "denied" ? text.prepareDenied : text.prepareUnavailable}
        </p>}
        <div><Button variant="primary" disabled={stage.status === "preparing" || !selected.size}
          onClick={() => void prepare(stage.context)}>
          {stage.status === "preparing" ? text.preparing : text.prepare}
        </Button></div>
      </>}
    </div>
  </Card>;
}
