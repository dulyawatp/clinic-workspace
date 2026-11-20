import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useNavigate } from "react-router-dom";
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

function encodeNote(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

type NoteEdit = { original: LinkedNote | null; text: string };
type Operation = "save" | "submit" | "check" | "notes";

function LinkedVisit({ initial, appointmentRef }: { initial: LinkedEncounter; appointmentRef: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [record, setRecord] = useState(initial);
  const [clinical, setClinical] = useState<LinkedClinical>({});
  const [edit, setEdit] = useState<NoteEdit | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<Operation | null>(null);
  const [uncertain, setUncertain] = useState<"submit" | "save" | null>(null);
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

  const { context, encounter, notes } = record;
  const completed = encounter?.docstatus === 1;
  useEffect(() => {
    if (confirm) confirmation.current?.focus();
    else if (completed && initial.encounter?.docstatus === 0) patientHeading.current?.focus();
  }, [confirm, completed, initial.encounter?.docstatus]);

  const locked = Boolean(busy || uncertain || confirm);
  const hasClinicalChanges = Object.keys(clinical).length > 0;
  const dirty = hasClinicalChanges || edit !== null;
  const noteChanged = edit && edit.text !== (edit.original ? noteContent(edit.original.text).text : "");
  const cannotSave = (encounter !== null && !hasClinicalChanges && !noteChanged) ||
    Boolean(edit && !edit.text.trim());

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
    setUncertain(null);
    setClinical({});
    setEdit(null);
    setPageError(false);
    setRecord(next);
    if (next.encounter && (!encounter || next.encounter.docstatus === 1)) {
      void navigate(ROUTES.linkedVisit(appointmentRef, next.encounter.id), { replace: true });
    }
  }

  function verifyIdentity(next: LinkedEncounter) {
    if (!sameContext(record, next) || (encounter && next.encounter?.id !== encounter.id)) throw invalidLinkedEncounter;
  }

  async function reconcile(kind: "submit" | "save", signal: AbortSignal) {
    try {
      const next = await openLinkedEncounter(appointmentRef, signal);
      verifyIdentity(next);
      if (signal.aborted) return;
      accept(next);
      setFeedback(next.encounter?.docstatus === 1 ? null :
        kind === "submit" ? "linkedEncounter.stillDraft" : "linkedEncounter.saveUnconfirmed");
    } catch {
      if (!signal.aborted) setFeedback("linkedEncounter.statusUnconfirmed");
    }
  }

  function persist(kind: "submit" | "save") {
    if (completed || uncertain || (kind === "submit" && (!encounter || dirty || !confirm))) return;
    if (kind === "save" && (confirm || cannotSave || (noteChanged && notes.state !== "available"))) return;
    void run(kind, async (signal) => {
      setFeedback(null);
      try {
        const next = kind === "submit" && encounter
          ? await completeLinkedEncounter(appointmentRef, encounter.id, encounter.modified)
          : await saveLinkedEncounter(appointmentRef, encounter?.modified ?? null, {
            ...clinical,
            ...(noteChanged && { note: { id: edit.original?.id ?? null, modified: edit.original?.modified ?? null, text: encodeNote(edit.text) } }),
          });
        verifyIdentity(next);
        if (next.encounter?.docstatus !== (kind === "submit" ? 1 : 0)) throw invalidLinkedEncounter;
        if (signal.aborted) return;
        accept(next);
        if (kind === "save" && encounter) setFeedback("linkedEncounter.saved");
      } catch (error) {
        if (signal.aborted) return;
        setConfirm(false);
        const code = (error as EncounterReadError | undefined)?.code;
        if (code === "FORBIDDEN" || code === "AUTH_EXPIRED") {
          setFeedback("linkedEncounter.writeDenied");
        } else if (code === "VALIDATION" || code === "CONFLICT_STALE") {
          setFeedback("linkedEncounter.writeRejected");
          setUncertain(kind);
        } else {
          setUncertain(kind);
          await reconcile(kind, signal);
        }
      }
    });
  }

  function loadNotes() {
    if (notes.next_offset === null || locked) return;
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
    return (
      <div className="visit-preview-card visit-preview-actions visit-preview-stack">
        <p role={feedback ? "alert" : "status"}>{t(feedback ?? "linkedEncounter.checking")}</p>
        <div><Button disabled={Boolean(busy)} onClick={() => void run("check", (signal) => reconcile(uncertain, signal))}>
          {t(busy ? "common.loading" : "linkedEncounter.check")}
        </Button></div>
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
          {(["symptoms", "diagnoses"] as const).map((kind) => completed
            ? <SavedTerms key={kind} kind={kind} items={encounter[kind]} />
            : <ClinicalMasterPicker key={kind} kind={kind} selected={clinical[kind] ?? encounter?.[kind] ?? []}
              disabled={locked} onChange={(values) => setClinical((prev) => ({ ...prev, [kind]: values }))} />)}
        </div>}
        note={notes.state === "unavailable" ? <p className="pc-note">{t("workspace.savedNoteUnavailable")}</p> :
          <div className="visit-preview-stack">
            {!notes.items.length && <p className="pc-note">{t("linkedEncounter.noNotes")}</p>}
            {notes.items.map((item) => {
              const content = noteContent(item.text);
              return <article className="visit-preview-stack visit-linked-note" key={item.id}>
                <h4>{item.id}</h4>
                <p className="visit-linked-note-text">{content.text || t("linkedEncounter.noNoteText")}</p>
                {content.rich && <p className="pc-note">{t("linkedEncounter.formattedNote")}</p>}
                {!content.rich && !completed && <div>
                  <Button variant="secondary" disabled={locked || edit !== null}
                    onClick={() => setEdit({ original: item, text: content.text })}>
                    {t("linkedEncounter.editNote", { id: item.id })}
                  </Button>
                </div>}
              </article>;
            })}
            {pageError && <p role="alert">{t("linkedEncounter.notesLoadFailed")}</p>}
            {notes.next_offset !== null && <div><Button variant="secondary" disabled={locked || edit !== null} onClick={loadNotes}>
              {t(busy === "notes" ? "common.loading" : "linkedEncounter.moreNotes")}
            </Button></div>}
            {!completed && (edit ? (
              <div className="pc-field">
                <label>{t("workspace.narrative")}
                  <textarea value={edit.text} disabled={locked} onChange={(event) => setEdit({ ...edit, text: event.target.value })} />
                </label>
                <p className="pc-note">{t("linkedEncounter.noteSaveHint")}</p>
                <div><Button variant="secondary" disabled={locked} onClick={() => setEdit(null)}>
                  {t("linkedEncounter.discardNote")}
                </Button></div>
              </div>
            ) : <div><Button variant="secondary" disabled={locked} onClick={() => setEdit({ original: null, text: "" })}>
              {t("linkedEncounter.addNote")}
            </Button></div>)}
          </div>}
        medications={<p className="pc-note">{t("workspace.savedMedicationsUnavailable")}</p>}
      />
      {(feedback || !completed) && (
        <div className="visit-preview-card visit-preview-actions visit-preview-stack">
          {feedback && <p role={feedback === "linkedEncounter.saved" ? "status" : "alert"}>{t(feedback)}</p>}
          {confirm ? (
            <div role="group" aria-labelledby={confirmLabel} className="visit-preview-stack" ref={confirmation} tabIndex={-1}>
              <p id={confirmLabel}>{t("linkedEncounter.confirmHint")}</p>
              <div className="pc-row">
                <Button variant="primary" disabled={Boolean(busy)} onClick={() => persist("submit")}>{t("linkedEncounter.confirm")}</Button>
                <Button variant="secondary" disabled={Boolean(busy)} onClick={() => setConfirm(false)}>{t("linkedEncounter.back")}</Button>
              </div>
            </div>
          ) : !completed && <>
            {encounter && dirty && <p className="pc-note">{t("linkedEncounter.saveBeforeSubmit")}</p>}
            <div className="pc-row">
              <Button variant="primary" disabled={locked || cannotSave}
                onClick={() => persist("save")}>{t(busy === "save" ? "linkedEncounter.saving" : "linkedEncounter.save")}</Button>
              {encounter && <Button variant="secondary" disabled={locked || dirty} onClick={() => setConfirm(true)}>
                {t("linkedEncounter.submit")}
              </Button>}
            </div>
          </>}
        </div>
      )}
    </div>
  );
}
