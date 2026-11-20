import { useQuery } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, Button, Drawer, SampleBanner } from "../../design/components";
import { toI18nKey } from "../../domain/errors";
import { coreData, type Visit } from "../../fixtures/data";
import { translateClinicalText } from "../../i18n";
import { formatDate, formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { type EncounterReadError, type NativeEncounterDetail, type NativeEncounterSummary, viewNativeEncounter } from "../../transport/native/encounter-read";
import { StatePanel } from "../../ui/StatePanel";
import { SampleLabFiles, SamplePhotos } from "./VisitEvidencePreview";
import { approvedVisitNarrative, SAMPLE_REPORT, sampleLabFiles, samplePhotoSides, type SampleHistoryPreview } from "./visit-preview";
import { PhotosSection } from "./VisitSampleSections";

type Kind = "symptoms" | "diagnoses";
const terms = {
  symptoms: ["acne", "itching", "redness", "drySkin"],
  diagnoses: ["acneVulgaris", "dermatitis", "rosacea"],
};

function TermPicker({ kind, selected, onChange, locked }: {
  kind: Kind;
  selected: string[];
  onChange: (next: string[]) => void;
  locked: boolean;
}) {
  const { t, i18n } = useTranslation();
  const id = useId();
  const [query, setQuery] = useState("");
  const matches = terms[kind]
    .map((term) => t(`workspace.terms.${term}`))
    .filter((term) => !selected.includes(term) &&
      term.toLocaleLowerCase(i18n.language).includes(query.trim().toLocaleLowerCase(i18n.language)));
  const choose = (term: string) => {
    onChange([...selected, term]);
    setQuery("");
  };
  return (
    <div className="visit-preview-stack">
      <div className="pc-field">
        <label htmlFor={id}>{t(`workspace.${kind}`)}</label>
        {!locked && (
          <input id={id} type="search" value={query} placeholder={t("workspace.searchTerms")}
            onChange={(event) => setQuery(event.target.value)} />
        )}
      </div>
      {!locked && query.trim() && (
        <ul className="visit-preview-choices">
          {matches.map((term) => (
            <li key={term}><button type="button" onClick={() => choose(term)}>{term}</button></li>
          ))}
          {matches.length === 0 && <li className="pc-note">{t("workspace.noTerms")}</li>}
        </ul>
      )}
      <ul className="visit-preview-chips">
        {selected.map((term) => (
          <li key={term}>
            {term}
            {!locked && (
              <button type="button" aria-label={t("workspace.removeTerm", { term })}
                onClick={() => onChange(selected.filter((item) => item !== term))}>×</button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EncounterLayout({ status, details, detailsLabel, note, medications, photos, lab }: {
  status: ReactNode;
  details: ReactNode;
  detailsLabel?: string;
  note?: ReactNode;
  medications?: ReactNode;
  photos?: ReactNode;
  lab?: ReactNode;
}) {
  const { t } = useTranslation();
  const sections = [
    ["encounter-details", "workspace.details", details],
    ["clinical-note", "workspace.note", note ?? <p className="pc-note">{t("workspace.nativeNoteUnavailable")}</p>],
    ["medications", "workspace.medications", medications ?? <p className="pc-note">{t("encounterWrite.medicationsUnavailable")}</p>],
  ] as const;
  return (
    <div className="visit-preview-layout">
      <article className="visit-preview-card visit-preview-main" aria-labelledby="patient-encounter">
        <div className="visit-preview-head">
          <h2 id="patient-encounter">{t("workspace.encounter")}</h2>{status}
        </div>
        <nav className="visit-preview-jump" aria-label={t("workspace.sections")}>
          {sections.map(([id, key]) => <a key={id} href={`#${id}`}>{id === "encounter-details" ? detailsLabel ?? t("workspace.coded") : t(key)}</a>)}
        </nav>
        {sections.map(([id, key, body], index) => (
          <section key={id} className={`visit-preview-section ${id === "clinical-note" ? "visit-preview-note" : ""}`} aria-labelledby={id}>
            <div className="visit-preview-heading">
              <span aria-hidden="true">{`0${index + 1}`}</span><h3 tabIndex={-1} id={id}>{t(key)}</h3>
              {id === "clinical-note" && <Badge tone="info">{t("workspace.separateRecord")}</Badge>}
            </div>
            {body}
          </section>
        ))}
      </article>
      <aside className="visit-preview-rail" aria-label={t("workspace.context")}>
        <section className="visit-preview-card visit-preview-side" aria-labelledby="visit-photo-heading">
          <h2 id="visit-photo-heading">{t("workspace.photos")}</h2>
          {photos ?? <p className="pc-note">{t("workspace.nativePhotosUnavailable")}</p>}
        </section>
        <section className="visit-preview-card visit-preview-side" aria-labelledby="visit-lab-heading">
          <div className="visit-preview-head">
            <h2 id="visit-lab-heading">{t("workspace.lab")}</h2><Badge tone="neutral">{t("workspace.privateAttachment")}</Badge>
          </div>
          {lab ?? <p className="pc-note">{t("workspace.nativeLabUnavailable")}</p>}
        </section>
      </aside>
    </div>
  );
}

export function EncounterStatus({ status }: { status: NativeEncounterSummary["docstatus"] }) {
  const { t } = useTranslation();
  const [label, tone] = ([
    ["status.draft", "warning"],
    ["status.submitted", "success"],
    ["status.cancelled", "neutral"],
  ] as const)[status];
  return <Badge tone={tone}>{t(label)}</Badge>;
}

export function EncounterReadFailure({ error, onRetry }: {
  error: EncounterReadError;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  if (error.code === "FORBIDDEN")
    return <StatePanel kind="permission-denied" title={t("state.denied.title")} />;
  const title = t(error.code === "MALFORMED" ? "workspace.nativeEncounterLoadError" : toI18nKey(error));
  return error.retryable
    ? <StatePanel kind="error" title={title} onRetry={onRetry} retryLabel={t("action.retry")} />
    : <StatePanel kind="error" title={title} />;
}

export function SavedTerms({ kind, items }: { kind: Kind; items: string[] | null }) {
  const { t } = useTranslation();
  return (
    <div className="visit-preview-stack">
      <h4>{t(`workspace.${kind}`)}</h4>
      {items?.length ? (
        <ul className="visit-saved-terms">
          {items.map((term, index) => <li key={`${index}:${term}`}>{term}</li>)}
        </ul>
      ) : (
        <p className="pc-note">{t(`workspace.${kind}.${items === null ? "unavailable" : "empty"}`)}</p>
      )}
    </div>
  );
}

export function SavedEncounterWorkspace({ encounterId }: { encounterId: string }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { authGeneration } = useSession();
  const query = useQuery<NativeEncounterDetail | null, EncounterReadError>({
    queryKey: ["native-saved-encounter", authGeneration, encounterId],
    queryFn: ({ signal }) => viewNativeEncounter(encounterId, signal),
    retry: false,
    gcTime: 0,
  });
  if (query.isError)
    return <EncounterReadFailure error={query.error} onRetry={() => void query.refetch()} />;
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const encounter = query.data;
  if (!encounter) return <StatePanel kind="empty" title={t("clinical.notFound")} />;
  const unavailable = t("common.unavailable");
  return (
    <div className="visit-workspace visit-preview">
      <header className="visit-identity visit-preview-identity">
        <div className="visit-preview-head">
          <div className="visit-identity-heading">
            <span className="visit-avatar" aria-hidden="true">✚</span>
            <div>
              <p>{t("clinical.patient")} · {encounter.patientId}</p>
              <h2>{encounter.patientName}</h2>
            </div>
          </div>
          <Link className="pc-button pc-button--secondary" to={ROUTES.patientHistory(encounter.patientId)}>
            {t("workspace.patientHistory")}
          </Link>
        </div>
        <dl>
          <div><dt>{t("clinical.encounterId")}</dt><dd>{encounter.id}</dd></div>
          <div><dt>{t("clinical.practitioner")}</dt><dd>{encounter.practitioner || unavailable}</dd></div>
          <div><dt>{t("field.date")}</dt><dd>{encounter.encounterDate ? formatDate(encounter.encounterDate, locale) : unavailable}</dd></div>
        </dl>
      </header>
      <EncounterLayout
        status={<EncounterStatus status={encounter.docstatus} />}
        details={<div className="visit-preview-stack">
          <SavedTerms kind="symptoms" items={encounter.symptoms} />
          <SavedTerms kind="diagnoses" items={encounter.diagnoses} />
        </div>}
        note={<p className="pc-note">{t("workspace.savedNoteUnavailable")}</p>}
        medications={<p className="pc-note">{t("workspace.savedMedicationsUnavailable")}</p>}
      />
    </div>
  );
}

const sampleMedication = {
  "DEMO-TOPICAL-01": "workspace.medication.sampleTopical",
  "DEMO-ORAL-01": "workspace.medication.sampleOral",
};
const emptyMedication = { item: "", dose: "", form: "", freq: "", duration: "", mode: "preset", unit: "hour" };
const medicationFields = ["dose", "form", "duration"] as const;

export function ApprovedVisitWorkspace({ visit }: { visit: Visit }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const patient = coreData.patientById(visit.patientId);
  const confirmedVisit = visit.appointmentId ? coreData.appointmentById(visit.appointmentId) : null;
  const canSimulatePhoto = patient?.consent.photo === true &&
    confirmedVisit?.patientId === visit.patientId &&
    confirmedVisit.visitId === visit.id && Boolean(confirmedVisit.checkedInAt);
  const [symptoms, setSymptoms] = useState(() => {
    const complaint = visit.history.complaint ?? (visit.history.complaintKey && t(visit.history.complaintKey));
    return complaint ? [translateClinicalText(complaint)] : [];
  });
  const [diagnoses, setDiagnoses] = useState(() => visit.diagnoses.map((row) => row.label[locale]));
  const [comment, setComment] = useState("");
  const [narrative, setNarrative] = useState(() => approvedVisitNarrative(visit, t));
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [savedDraft, setSavedDraft] = useState<string | null>(null);
  const [locked, setLocked] = useState(visit.state === "completed");
  const [review, setReview] = useState(false);
  const [medications, setMedications] = useState(() => visit.meds.map((row, id) => ({ ...emptyMedication, ...row, id })));
  const [nextMedicationId, setNextMedicationId] = useState(visit.meds.length);
  const [medication, setMedication] = useState<{ id: number | null; values: typeof emptyMedication } | null>(null);
  const [labFiles, setLabFiles] = useState(() => sampleLabFiles(visit));
  const [labAttachOpen, setLabAttachOpen] = useState(false);
  const [sides, setSides] = useState(() => samplePhotoSides(visit));
  const historyPreview: SampleHistoryPreview = { visitId: visit.id, reportAdded: labFiles.includes(SAMPLE_REPORT), sides, savedNote };
  const draft = JSON.stringify([symptoms, diagnoses, comment, medications]);
  const setTerms = { symptoms: setSymptoms, diagnoses: setDiagnoses };
  const choices = { symptoms, diagnoses };
  const allergy = patient?.allergies.status === "known"
    ? patient.allergies.items.map((item) => item[locale]).join(", ")
    : t(patient?.allergies.status === "none" ? "clinical.allergyNone" : "clinical.allergyUnknown");

  function saveMedication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!medication) return;
    const id = medication.id ?? nextMedicationId;
    const row = { ...medication.values, id, status: "prescribed" as const };
    setMedications((current) => medication.id === null ? [...current, row] :
      current.map((previous) => previous.id === id ? row : previous));
    if (medication.id === null) setNextMedicationId(id + 1);
    setMedication(null);
  }

  return (
    <div className="visit-workspace visit-preview" data-testid="sample-visit-workspace" data-surface="DOC-02" data-mode="demo">
      <SampleBanner label={t("app.demoTag")} title={t("workspace.sampleEncounter")}>
        <p className="pc-note">{t("workspace.sampleScope")}</p>
      </SampleBanner>
      <header className="visit-identity visit-preview-identity">
        <div className="visit-preview-head">
          <div className="visit-identity-heading">
            <span className="visit-avatar" aria-hidden="true">✚</span>
            <div><p>{t("clinical.patient")} · {visit.patientId}</p><h2>{coreData.patientName(visit.patientId, locale)}</h2></div>
          </div>
          <div className="pc-row visit-preview-hero-actions">
            <Link className="pc-button pc-button--secondary" to={`${ROUTES.patient(visit.patientId)}?view=history&visit=${encodeURIComponent(visit.id)}`}
              state={{ historyPreview }}>
              {t("workspace.history")}
            </Link>
            <Link className="pc-button pc-button--secondary" to={ROUTES.photos(visit.patientId)}>{t("visit.patientPhotos")}</Link>
          </div>
        </div>
        <dl>
          <div><dt>{t("clinical.encounterId")}</dt><dd data-testid="encounter-id">{visit.id}</dd></div>
          <div><dt>{t("clinical.started")}</dt><dd>{formatDateTime(visit.date, locale)}</dd></div>
          <div><dt>{t("clinical.practitioner")}</dt><dd>{coreData.practitionerById(visit.practitioner).name[locale]}</dd></div>
          <div><dt>{t("clinical.appointment")}</dt><dd>{visit.appointmentId ?? "—"}</dd></div>
        </dl>
      </header>
      <div className="visit-preview-alert"><strong>{t("clinical.allergies")}:</strong> {allergy}</div>
      <EncounterLayout
        status={<Badge tone={locked ? "success" : "warning"}>{t(locked ? "status.completed" : "status.draft")}</Badge>}
        details={
          <>
            <p className="pc-note">{t("workspace.sampleTerms")}</p>
            <div className="visit-preview-fields">
              {(["symptoms", "diagnoses"] as const).map((kind) => (
                <TermPicker key={kind} kind={kind} selected={choices[kind]} onChange={setTerms[kind]} locked={locked} />
              ))}
            </div>
            <details className="visit-preview-details">
              <summary>{t("workspace.moreDetails")}</summary>
              <label className="pc-field">
                {t("workspace.comment")}
                <textarea value={comment} onChange={(event) => setComment(event.target.value)} readOnly={locked} />
              </label>
              <p className="pc-note">{t("workspace.commentScope")}</p>
            </details>
          </>
        }
        note={
          <>
            <p className="pc-note">{t("workspace.noteScope")}</p>
            <p id="visit-narrative-prompts" className="visit-preview-prompts">{t("workspace.notePrompts")}</p>
            <label className="pc-field">
              {t("workspace.narrative")}
              <textarea rows={6} aria-describedby="visit-narrative-prompts" value={narrative}
                onChange={(event) => setNarrative(event.target.value)} />
            </label>
            <div className="pc-row">
              <Button variant="secondary" onClick={() => setSavedNote(narrative)}>{t("workspace.saveNote")}</Button>
              <span role="status" className="pc-note">{t(savedNote !== null && savedNote === narrative ? "workspace.noteSaved" : "workspace.noteUnsaved")}</span>
            </div>
          </>
        }
        medications={
          <>
            <div className="visit-preview-head">
              <p className="pc-note">{t("workspace.medicationScope")}</p>
              {!locked && <Button variant="primary" onClick={() => setMedication({ id: null, values: emptyMedication })}>{t("workspace.addMedication")}</Button>}
            </div>
            {medications.length > 0 ? (
              <ul className="visit-preview-files">
                {medications.map((row) => (
                  <li key={row.id} className="visit-preview-medication">
                    <div>
                      <strong>{Object.hasOwn(sampleMedication, row.item) ? t(sampleMedication[row.item as keyof typeof sampleMedication]) : translateClinicalText(row.item)}</strong>
                      <p className="pc-note">{[
                        row.dose, row.form || row.route,
                        row.mode === "interval" ? t(`workspace.medication.every.${row.unit}`, { count: Number(row.freq) }) : row.freq,
                        row.duration,
                      ].filter((part): part is string => Boolean(part)).map(translateClinicalText).join(" · ")}</p>
                    </div>
                    {!locked && <div className="pc-row">
                      <Button variant="secondary" onClick={() => setMedication({ id: row.id, values: row })}>{t("workspace.editMedication", { item: row.item })}</Button>
                      <Button variant="secondary" onClick={() => setMedications((current) => current.filter((item) => item.id !== row.id))}>{t("workspace.removeMedication", { item: row.item })}</Button>
                    </div>}
                  </li>
                ))}
              </ul>
            ) : <p className="pc-note">{t("clinical.noMedications")}</p>}
          </>
        }
        photos={
          <>
            <p className="pc-note">{t("workspace.sampleImages")}</p>
            <SamplePhotos sides={sides} />
            {canSimulatePhoto ? <div className="pc-row">
              {(["before", "after"] as const).filter((side) => !sides.includes(side)).map((side) =>
                <Button key={side} variant="secondary" onClick={() => setSides((previous) => [...previous, side])}>{t(`workspace.addPhoto.${side}`)}</Button>)}
            </div> : <p className="pc-note">{t("workspace.samplePhotoUnavailable")}</p>}
            <PhotosSection patientId={visit.patientId} />
          </>
        }
        lab={
          <>
            <p className="pc-note">{t("workspace.labScope")}</p>
            <SampleLabFiles files={labFiles} />
            {!labFiles.includes(SAMPLE_REPORT) && <Button variant="secondary" onClick={() => setLabAttachOpen(true)}>{t("workspace.attachReport")}</Button>}
          </>
        }
      />
      <div className="visit-save-footer visit-preview-footer">
        <p role="status" className="pc-status-line" data-testid="visit-save-status">
          {t(locked ? "visit.readinessLocked" : savedDraft === draft ? "workspace.draftSaved" : "workspace.draftUnsaved")}
        </p>
        <div className="pc-row">
          <Button variant="secondary" disabled={locked} onClick={() => setSavedDraft(draft)}>{t("action.saveDraft")}</Button>
          <Button variant="primary" disabled={locked} onClick={() => setReview(true)}>{t("workspace.reviewDraft")}</Button>
        </div>
      </div>
      {review && (
        <Drawer title={t("workspace.reviewDraft")} onClose={() => setReview(false)}
          footer={<Button variant="primary" onClick={() => { setLocked(true); setReview(false); }}>{t("visit.completeAction")}</Button>}>
          <div className="visit-preview-stack">
            <p>{t("workspace.reviewScope")}</p>
            <dl className="pc-dl">
              {(["symptoms", "diagnoses"] as const).map((kind) => <div key={kind}><dt>{t(`workspace.${kind}`)}</dt><dd>{choices[kind].join(" · ") || "—"}</dd></div>)}
              <div><dt>{t("workspace.comment")}</dt><dd>{comment || "—"}</dd></div>
              <div><dt>{t("workspace.medications")}</dt><dd>{medications.map((row) => row.item).join(" · ") || "—"}</dd></div>
            </dl>
          </div>
        </Drawer>
      )}
      {medication && (
        <Drawer title={t("workspace.medications")} onClose={() => setMedication(null)}>
          <form className="visit-preview-stack" onSubmit={saveMedication}>
            <p className="pc-note">{t("workspace.medicationScope")}</p>
            <div className="visit-preview-fields">
              <label className="pc-field">
                {t("workspace.medication.item")}
                <select required value={medication.values.item} onChange={(event) =>
                  setMedication({ ...medication, values: { ...medication.values, item: event.target.value } })}>
                  <option value="">{t("workspace.medication.choose")}</option>
                  {medication.id !== null && medication.values.item &&
                    !Object.hasOwn(sampleMedication, medication.values.item) &&
                    <option value={medication.values.item}>{medication.values.item}</option>}
                  {Object.entries(sampleMedication).map(([item, key]) => <option key={item} value={item}>{t(key)}</option>)}
                </select>
              </label>
              <label className="pc-field">{t("workspace.medication.code")}<input readOnly value={medication.values.item} /></label>
              <label className="pc-field">
                {t("workspace.medication.schedule")}
                <select value={medication.values.mode} onChange={(event) =>
                  setMedication({ ...medication, values: { ...medication.values, mode: event.target.value, freq: "" } })}>
                  <option value="preset">{t("workspace.medication.preset")}</option>
                  <option value="interval">{t("workspace.medication.interval")}</option>
                </select>
              </label>
              <label className="pc-field">
                {t(medication.values.mode === "interval" ? "workspace.medication.interval" : "workspace.medication.freq")}
                <input required type={medication.values.mode === "interval" ? "number" : "text"} min={1} step={1}
                  value={medication.values.freq} onChange={(event) =>
                    setMedication({ ...medication, values: { ...medication.values, freq: event.target.value } })} />
              </label>
              {medication.values.mode === "interval" && (
                <label className="pc-field">
                  {t("workspace.medication.unit")}
                  <select value={medication.values.unit} onChange={(event) =>
                    setMedication({ ...medication, values: { ...medication.values, unit: event.target.value } })}>
                    <option value="hour">{t("workspace.medication.hour")}</option>
                    <option value="day">{t("workspace.medication.day")}</option>
                  </select>
                </label>
              )}
              {medicationFields.map((field) => (
                <label key={field} className="pc-field">
                  {t(`workspace.medication.${field}`)}
                  <input required value={medication.values[field]} onChange={(event) => setMedication({
                    ...medication, values: { ...medication.values, [field]: event.target.value },
                  })} />
                </label>
              ))}
            </div>
            <Button variant="primary" type="submit">{t("workspace.keepMedication")}</Button>
          </form>
        </Drawer>
      )}
      {labAttachOpen && (
        <Drawer title={t("workspace.attachReport")} onClose={() => setLabAttachOpen(false)}
          footer={<Button variant="primary" onClick={() => { setLabFiles((current) => [...current, SAMPLE_REPORT]); setLabAttachOpen(false); }}>{t("workspace.attachReport")}</Button>}>
          <p>{t("workspace.sampleReport")}</p>
          <p>{SAMPLE_REPORT}</p>
        </Drawer>
      )}
    </div>
  );
}
