import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useBeforeUnload, useNavigate } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, Button } from "../../design/components";
import type { EncounterReadError } from "../../transport/native/encounter-read";
import {
  completeLinkedEncounter,
  invalidLinkedEncounter,
  type LinkedClinical,
  type LinkedEncounter,
  type LinkedNote,
  openLinkedEncounter,
  saveLinkedEncounter,
  searchClinicalMaster,
} from "../../transport/native/linked-encounter";
import { StatePanel } from "../../ui/StatePanel";
import { EncounterLayout, EncounterReadFailure, EncounterStatus, SavedTerms } from "./ApprovedVisitWorkspace";
import { FormattedNoteEditor, FormattedNotePreview } from "./LinkedFormattedNote";
import { LinkedPrescriptions, prescriptionChange, type PrescriptionDraft, PrescriptionRecovery } from "./LinkedPrescriptions";
import { encodeNoteText, notePlainText, type NoteLine, parseFormattedNote, serializeFormattedNote } from "./linked-note-format";

type Kind = "symptoms" | "diagnoses";

function ClinicalMasterPicker({ kind, selected, onChange, disabled }: {
  kind: Kind;
  selected: string[];
  onChange: (values: string[]) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const label = useId();
  const [search, setSearch] = useState("");
  const [settled, setSettled] = useState("");
  const text = search.trim();
  const doctype = kind === "symptoms" ? "Complaint" : "Diagnosis";
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(text), 250);
    return () => window.clearTimeout(timer);
  }, [text]);
  const query = useQuery({
    queryKey: ["clinical-master", authGeneration, doctype, settled],
    queryFn: ({ signal }) => searchClinicalMaster(doctype, settled, signal),
    enabled: Boolean(settled) && !disabled,
    retry: false,
    gcTime: 0,
  });
  const loading = settled !== text || query.isFetching;
  const matches = query.data?.filter((value) => !selected.includes(value)) ?? [];
  return (
    <div className="visit-preview-stack">
      <div className="pc-field">
        <label htmlFor={label}>{t(`workspace.${kind}`)}</label>
        <input id={label} type="search" value={search} disabled={disabled} maxLength={100}
          placeholder={t("linkedEncounter.search")} onChange={(event) => setSearch(event.target.value)} />
      </div>
      {!disabled && text && (
        loading ? <p className="pc-note" role="status">{t("common.loading")}</p> :
        query.isError ? <p className="pc-note" role="status">{t("linkedEncounter.searchUnavailable")}</p> :
        matches.length === 0 ? <p className="pc-note" role="status">{t("linkedEncounter.noMatches")}</p> :
        <ul className="visit-preview-choices">
          {matches.map((value) => (
            <li key={value}><button type="button" onClick={() => {
              onChange([...selected, value]);
              setSearch("");
            }}>{value}</button></li>
          ))}
        </ul>
      )}
      {selected.length > 0 && <ul className="visit-preview-chips">
        {selected.map((value) => (
          <li key={value}>{value}<button type="button" disabled={disabled}
            aria-label={t("workspace.removeTerm", { term: value })}
            onClick={() => onChange(selected.filter((item) => item !== value))}>×</button></li>
        ))}
      </ul>}
    </div>
  );
}

function sameContext(a: LinkedEncounter, b: LinkedEncounter): boolean {
  return a.context.appointment === b.context.appointment && a.context.patient === b.context.patient &&
    a.context.practitioner === b.context.practitioner;
}

export function LinkedVisitWorkspace({ appointmentRef, encounterId }: {
  appointmentRef: string;
  encounterId: string | null;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const query = useQuery<LinkedEncounter, EncounterReadError>({
    queryKey: ["linked-encounter", authGeneration, appointmentRef, encounterId],
    queryFn: async ({ signal }) => {
      const result = await openLinkedEncounter(appointmentRef, signal);
      if (encounterId !== null && result.encounter?.id !== encounterId) throw invalidLinkedEncounter;
      return result;
    },
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (query.isError) return <EncounterReadFailure error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data || !query.isFetchedAfterMount) return <StatePanel kind="loading" title={t("common.loading")} />;
  if (encounterId === null && query.data.encounter) {
    return <Navigate replace to={ROUTES.linkedVisit(appointmentRef, query.data.encounter.id)} />;
  }
  return <LinkedVisit initial={query.data} appointmentRef={appointmentRef} />;
}

// Template contents are inert and never inserted into the document. Render only
// text, not native HTML, and don't offer a plain-text replacement for markup.
function noteContent(source: string) {
  const template = document.createElement("template");
  template.innerHTML = source;
  const rich = /<[a-z!/]/i.test(source);
  function readable(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeType === Node.ELEMENT_NODE &&
        /^(script|style|template|noscript|iframe|object|svg)$/i.test(node.nodeName)) return "";
    const children = Array.from(node.childNodes, readable).join("");
    return /^(br|div|p|li|tr|h[1-6])$/i.test(node.nodeName) ? `${children}\n` : children;
  }
  return { text: rich ? readable(template.content).trim() : template.content.textContent ?? "", rich };
}

type NoteEdit = {
  original: LinkedNote | null;
  text: string;
  rich?: { lines: NoteLine[]; baseline: string; html: string | null };
};
type Write = "save" | "submit";
type Operation = Write | "check" | "notes";
type Uncertain = { kind: Write; rejected?: true };
type Recovery = { unavailable: boolean; uncertain: Uncertain | null };

// Same-tab recovery marker only. Do not persist record identifiers or any
// patient, note, or clinical data. This fingerprint is not authorization.
function pendingKey(kind: Write, id: string) {
  let fingerprint = 0xcbf29ce484222325n;
  for (let i = 0; i < id.length; i++) {
    fingerprint = BigInt.asUintN(64, (fingerprint ^ BigInt(id.charCodeAt(i))) * 0x100000001b3n);
  }
  return `clinic.linked.${kind}.${fingerprint.toString(16)}`;
}

function readMarker(key: string): "clear" | "pending" | "unavailable" {
  try {
    const value = sessionStorage.getItem(key);
    return value === null ? "clear" : value === "pending" ? "pending" : "unavailable";
  } catch {
    return "unavailable";
  }
}

function readRecovery(saveKey: string, draftSubmitKey: string | null): Recovery {
  const save = readMarker(saveKey);
  const submit = draftSubmitKey ? readMarker(draftSubmitKey) : "clear";
  const unavailable = save === "unavailable" || submit === "unavailable";
  const uncertain = unavailable ? null : submit === "pending" ? { kind: "submit" } as const :
    save === "pending" ? { kind: "save" } as const : null;
  return { unavailable, uncertain };
}

function clearPending(key: string) {
  try { sessionStorage.removeItem(key); }
  catch { /* Leave a conservative lock if storage becomes unavailable. */ }
}

function deskEncounterHref(id: string): string | null {
  if (id === "." || id === "..") return null;
  try {
    // Desk reserves the slug route's "view" segment; its explicit Form route
    // uses the same native doctype without the list-view ambiguity.
    return id === "view" ? "/desk/Form/Patient%20Encounter/view" : `/desk/patient-encounter/${encodeURIComponent(id)}`;
  } catch {
    return null;
  }
}

function LinkedVisit({ initial, appointmentRef }: { initial: LinkedEncounter; appointmentRef: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [record, setRecord] = useState(initial);
  const [clinical, setClinical] = useState<LinkedClinical>({});
  const [edit, setEdit] = useState<NoteEdit | null>(null);
  const [prescription, setPrescription] = useState<PrescriptionDraft | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<Operation | null>(null);
  const saveKey = pendingKey("save", initial.context.appointment);
  const submitKey = initial.encounter && pendingKey("submit", initial.encounter.id);
  const [recovery, setRecovery] = useState(() => readRecovery(saveKey, initial.encounter?.docstatus === 0 ? submitKey : null));
  const { unavailable: recoveryUnavailable, uncertain } = recovery;
  const [feedback, setFeedback] = useState<string | null>(null);
  const [pageError, setPageError] = useState(false);
  const inFlight = useRef(false);
  const readLifetime = useRef<AbortController | null>(null);
  const confirmLabel = useId();
  const confirmation = useRef<HTMLDivElement>(null);
  const patientHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    readLifetime.current = controller;
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (submitKey && initial.encounter?.docstatus === 1) clearPending(submitKey);
  }, [submitKey, initial.encounter?.docstatus]);

  const { context, encounter, notes, medications } = record;
  const completed = encounter?.docstatus === 1;
  const canReviewPrescriptions = medications.state === "available" && medications.complete;
  const deskHref = encounter && !canReviewPrescriptions ? deskEncounterHref(encounter.id) : null;
  useEffect(() => {
    if (confirm) confirmation.current?.focus();
    else if (completed && initial.encounter?.docstatus === 0) patientHeading.current?.focus();
  }, [confirm, completed, initial.encounter?.docstatus]);

  const readLocked = Boolean(busy || uncertain || confirm);
  const locked = readLocked || recoveryUnavailable;
  const readOnly = completed || recoveryUnavailable;
  const hasClinicalChanges = Object.keys(clinical).length > 0;
  const dirty = hasClinicalChanges || edit !== null || prescription !== null;
  const medication = prescription ? prescriptionChange(prescription, medications) : null;
  const noteChanged = edit && (edit.rich ? edit.rich.html !== null && edit.rich.html !== edit.rich.baseline :
    edit.text !== (edit.original ? noteContent(edit.original.text).text : ""));
  const saveInput: LinkedClinical = {
    ...clinical,
    ...(medication?.changes && { medications: medication.changes }),
    ...(noteChanged && { note: {
      id: edit.original?.id ?? null, modified: edit.original?.modified ?? null,
      text: edit.rich?.html ?? encodeNoteText(edit.text),
    } }),
  };
  const cannotSave = medication?.invalid || (encounter !== null && !hasClinicalChanges && !noteChanged && !medication?.changes) ||
    Boolean(edit && ((!edit.rich && !edit.text.trim()) || edit.rich?.html === null || (noteChanged && !edit.text.trim())));
  useBeforeUnload((event) => {
    if ((uncertain?.kind === "save" || busy === "save") && dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  async function run(operation: Operation, action: (signal: AbortSignal) => Promise<void>) {
    const signal = readLifetime.current?.signal;
    if (inFlight.current || !signal || signal.aborted) return;
    inFlight.current = true;
    setBusy(operation);
    try {
      await action(signal);
    } finally {
      inFlight.current = false;
      if (!signal.aborted) setBusy(null);
    }
  }

  function accept(next: LinkedEncounter) {
    setConfirm(false);
    setRecovery({ unavailable: false, uncertain: null });
    setClinical({});
    setEdit(null);
    setPrescription(null);
    setPageError(false);
    setRecord(next);
    if (next.encounter && (!encounter || next.encounter.docstatus === 1)) {
      void navigate(ROUTES.linkedVisit(appointmentRef, next.encounter.id), { replace: true });
    }
  }

  function verifyIdentity(next: LinkedEncounter) {
    if (!sameContext(record, next) || (encounter && next.encounter?.id !== encounter.id)) throw invalidLinkedEncounter;
  }

  async function reconcile({ kind, rejected }: Uncertain, signal: AbortSignal) {
    setFeedback(null);
    try {
      const next = await openLinkedEncounter(appointmentRef, signal);
      verifyIdentity(next);
      if (signal.aborted) return;
      if (kind === "submit" && (next.encounter?.docstatus === 1 || rejected)) {
        if (submitKey) clearPending(submitKey);
        accept(next);
      } else {
        // Current data, even identical values at a newer version, cannot
        // establish whether an ambiguous save request has finished.
        setFeedback(kind === "submit" ? "linkedEncounter.stillDraft" :
          dirty ? "linkedEncounter.saveUnconfirmed" : "linkedEncounter.saveUnverified");
      }
    } catch {
      if (!signal.aborted) setFeedback("linkedEncounter.statusUnconfirmed");
    }
  }

  function persist(kind: Write) {
    if (readOnly || uncertain || (kind === "submit" && (!encounter || !canReviewPrescriptions || dirty || !confirm))) return;
    if (kind === "save" && (confirm || cannotSave || (noteChanged && notes.state !== "available"))) return;
    const key = kind === "save" ? saveKey : submitKey;
    if (!key) return;
    void run(kind, async (signal) => {
      setFeedback(null);
      const currentRecovery = readRecovery(saveKey, submitKey);
      if (currentRecovery.unavailable || currentRecovery.uncertain) {
        setConfirm(false);
        setRecovery(currentRecovery);
        return;
      }
      try {
        sessionStorage.setItem(key, "pending");
        if (readMarker(key) !== "pending") throw new Error();
      } catch {
        clearPending(key);
        setConfirm(false);
        setRecovery({ unavailable: true, uncertain: null });
        return;
      }
      try {
        const next = kind === "submit" && encounter
          ? await completeLinkedEncounter(appointmentRef, encounter.id, encounter.modified)
          : await saveLinkedEncounter(appointmentRef, encounter?.modified ?? null, saveInput);
        verifyIdentity(next);
        if (next.encounter?.docstatus !== (kind === "submit" ? 1 : 0)) throw invalidLinkedEncounter;
        clearPending(key);
        if (signal.aborted) return;
        accept(next);
        if (kind === "save" && encounter) setFeedback("linkedEncounter.saved");
      } catch (error) {
        const code = (error as EncounterReadError | undefined)?.code;
        const denied = code === "FORBIDDEN" || code === "AUTH_EXPIRED";
        const rejected = code === "VALIDATION" || code === "CONFLICT_STALE";
        if (denied || rejected) clearPending(key);
        if (signal.aborted) return;
        setConfirm(false);
        if (denied) {
          setFeedback("linkedEncounter.writeDenied");
        } else if (rejected) {
          setFeedback("linkedEncounter.writeRejected");
          if (kind === "submit") setRecovery({ unavailable: false, uncertain: { kind, rejected: true } });
        } else {
          setRecovery({ unavailable: false, uncertain: { kind } });
          await reconcile({ kind }, signal);
        }
      }
    });
  }

  function loadNotes() {
    if (notes.next_offset === null || readLocked) return;
    const offset = notes.next_offset;
    void run("notes", async (signal) => {
      setPageError(false);
      try {
        const next = await openLinkedEncounter(appointmentRef, signal, offset);
        verifyIdentity(next);
        if (next.encounter?.modified !== encounter?.modified ||
            next.encounter?.docstatus !== encounter?.docstatus) throw invalidLinkedEncounter;
        if (signal.aborted) return;
        const newer = next.notes;
        const seen = new Set(notes.items.map(({ id }) => id));
        setRecord({ ...record, notes: newer.state === "unavailable" ? newer : {
          ...newer, items: [...notes.items, ...newer.items.filter(({ id }) => !seen.has(id))],
        } });
        if (newer.state === "unavailable") setEdit(null);
      } catch {
        if (!signal.aborted) setPageError(true);
      }
    });
  }

  if (uncertain) {
    const notice = feedback ?? (busy ? "linkedEncounter.checking" :
      uncertain.kind === "submit" ? "linkedEncounter.stillDraft" : "linkedEncounter.saveUnverified");
    return (
      <div className="visit-preview-card visit-preview-actions visit-preview-stack">
        <p role={busy && !feedback ? "status" : "alert"}>{t(notice)}</p>
        <div><Button disabled={Boolean(busy)} onClick={() => void run("check", (signal) => reconcile(uncertain, signal))}>
          {t(busy ? "common.loading" : "linkedEncounter.check")}
        </Button></div>
        {uncertain.kind === "save" && dirty && (
          <div className="visit-preview-stack">
            {(["symptoms", "diagnoses"] as const).map((kind) => clinical[kind] &&
              <SavedTerms key={kind} kind={kind} items={clinical[kind]} />)}
            {edit && <div className="pc-field">
              <label>{t("workspace.narrative")}<textarea readOnly value={edit.text} /></label>
            </div>}
            {prescription && <PrescriptionRecovery draft={prescription} />}
          </div>
        )}
      </div>
    );
  }
  if (!initial.encounter && encounter) return <StatePanel kind="loading" title={t("common.loading")} />;

  return (
    <div className="visit-workspace visit-preview">
      <header className="visit-identity visit-preview-identity">
        <div className="visit-preview-head">
          <div className="visit-identity-heading">
            <span className="visit-avatar" aria-hidden="true">✚</span>
            <div><p>{t("clinical.patient")}</p><h2 ref={patientHeading} tabIndex={-1}>{context.patient}</h2></div>
          </div>
          <Link className="pc-button pc-button--secondary" to={ROUTES.patientHistory(context.patient)}>
            {t("workspace.patientHistory")}
          </Link>
        </div>
        <dl>
          <div><dt>{t("liveAppointments.reference")}</dt><dd>{context.appointment}</dd></div>
          <div><dt>{t("clinical.practitioner")}</dt><dd>{context.practitioner}</dd></div>
          {encounter && <div><dt>{t("clinical.encounterId")}</dt><dd>{encounter.id}</dd></div>}
        </dl>
      </header>
      <EncounterLayout
        status={encounter ? <EncounterStatus status={encounter.docstatus} /> :
          <Badge tone="warning">{t("linkedEncounter.unsaved")}</Badge>}
        details={<div className="visit-preview-stack">
          {(["symptoms", "diagnoses"] as const).map((kind) => readOnly
            ? <SavedTerms key={kind} kind={kind} items={clinical[kind] ?? encounter?.[kind] ?? []} />
            : <ClinicalMasterPicker key={kind} kind={kind} selected={clinical[kind] ?? encounter?.[kind] ?? []}
              disabled={locked} onChange={(values) => setClinical((prev) => ({ ...prev, [kind]: values }))} />)}
        </div>}
        note={notes.state === "unavailable" ? <p className="pc-note">{t("workspace.savedNoteUnavailable")}</p> :
          <div className="visit-preview-stack">
            {!notes.items.length && <p className="pc-note">{t("linkedEncounter.noNotes")}</p>}
            {notes.items.map((item) => {
              const content = noteContent(item.text);
              const lines = content.rich ? parseFormattedNote(item.text) : null;
              return <article className="visit-preview-stack visit-linked-note" key={item.id}>
                <h4>{item.id}</h4>
                {lines ? <FormattedNotePreview lines={lines} /> :
                  <p className="visit-linked-note-text">{content.text || t("linkedEncounter.noNoteText")}</p>}
                {content.rich && !lines && <p className="pc-note">{t("linkedEncounter.formattedNote")}</p>}
                {(!content.rich || lines) && !readOnly && <div>
                  <Button variant="secondary" disabled={locked || edit !== null}
                    onClick={() => {
                      const html = lines && serializeFormattedNote(lines);
                      setEdit(lines && html
                        ? { original: item, text: notePlainText(lines), rich: { lines, baseline: html, html } }
                        : { original: item, text: content.text });
                    }}>
                    {t("linkedEncounter.editNote", { id: item.id })}
                  </Button>
                </div>}
              </article>;
            })}
            {pageError && <p role="alert">{t("linkedEncounter.notesLoadFailed")}</p>}
            {notes.next_offset !== null && <div><Button variant="secondary" disabled={readLocked || edit !== null} onClick={loadNotes}>
              {t(busy === "notes" ? "common.loading" : "linkedEncounter.moreNotes")}
            </Button></div>}
            {!completed && (edit ? (
              <div className="pc-field">
                {edit.rich ? <FormattedNoteEditor lines={edit.rich.lines} disabled={locked} onChange={(value) => {
                  setEdit((previous) => previous?.rich ? {
                    ...previous, text: value?.text ?? previous.text, rich: { ...previous.rich, html: value?.html ?? null },
                  } : previous);
                }} /> : <label>{t("workspace.narrative")}
                  <textarea value={edit.text} readOnly={recoveryUnavailable} disabled={readLocked}
                    onChange={(event) => setEdit({ ...edit, text: event.target.value })} />
                </label>}
                {!recoveryUnavailable && <>
                  <p className="pc-note">{t("linkedEncounter.noteSaveHint")}</p>
                  <div><Button variant="secondary" disabled={locked} onClick={() => setEdit(null)}>
                    {t("linkedEncounter.discardNote")}
                  </Button></div>
                </>}
              </div>
            ) : !recoveryUnavailable && <div><Button variant="secondary" disabled={locked} onClick={() => setEdit({ original: null, text: "" })}>
              {t("linkedEncounter.addNote")}
            </Button></div>)}
          </div>}
        medications={<LinkedPrescriptions medications={medications} draft={prescription} editable={!readOnly}
          locked={locked} onChange={setPrescription} />}
      />
       {(feedback || !completed) && (
        <div className="visit-preview-card visit-preview-actions visit-preview-stack">
          {recoveryUnavailable && <p role="alert">{t("linkedEncounter.recoveryUnavailable")}</p>}
          {feedback && <p role={feedback === "linkedEncounter.saved" ? "status" : "alert"}>{t(feedback)}</p>}
          {confirm && canReviewPrescriptions ? (
            <div role="group" aria-labelledby={confirmLabel} aria-describedby={`${confirmLabel}-orders`}
              className="visit-preview-stack" ref={confirmation} tabIndex={-1}>
              <p id={confirmLabel}>{t("linkedEncounter.confirmHint")}</p>
              <p id={`${confirmLabel}-orders`} className="pc-note">{t("linkedRx.submitWarning")}</p>
              <div className="pc-row">
                <Button variant="primary" disabled={Boolean(busy)} onClick={() => persist("submit")}>{t("linkedEncounter.confirm")}</Button>
                <Button variant="secondary" disabled={Boolean(busy)} onClick={() => setConfirm(false)}>{t("linkedEncounter.back")}</Button>
              </div>
            </div>
          ) : !readOnly && <>
            {encounter && dirty && <p className="pc-note">{t("linkedEncounter.saveBeforeSubmit")}</p>}
            <div className="pc-row">
              <Button variant="primary" disabled={locked || cannotSave}
                onClick={() => persist("save")}>{t(busy === "save" ? "linkedEncounter.saving" : "linkedEncounter.save")}</Button>
              {encounter && canReviewPrescriptions && <Button variant="secondary" disabled={locked || dirty} onClick={() => setConfirm(true)}>
                {t("linkedEncounter.submit")}
              </Button>}
            </div>
          </>}
          {!completed && encounter && !canReviewPrescriptions && <div className="visit-preview-stack">
            <p className="pc-note">{t("linkedRx.completeInMarley")}</p>
            {deskHref && <div><a className="pc-button pc-button--secondary" href={deskHref}>{t("linkedRx.openInMarley")}</a></div>}
          </div>}
        </div>
      )}
    </div>
  );
}
