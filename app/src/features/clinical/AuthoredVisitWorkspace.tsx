import { type ChangeEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Drawer, SampleBanner } from "../../design/components";
import type { VisitSection } from "../../domain/clinical";
import { coreData, type Patient, type Visit } from "../../fixtures/data";
import { translateClinicalText } from "../../i18n";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useCapabilities } from "../shell/CapabilityProvider";
import {
  amendNativeEncounter,
  completeNativeEncounter,
  NativeEncounterWriteError,
  saveNativeEncounterDraft,
} from "../../transport/native/encounter-write";
import {
  type AuthoredVisitValues,
  checkCompletion,
  emptyVisitValues,
  initialVisitValues,
  outOfRangeVitals,
  readinessOf,
  VITAL_FIELD_KEYS,
  VITAL_RANGES,
} from "./authored-visit";
import { ExternalLabSection } from "./LabPanels";
import { SectionFrame } from "./SectionFrame";
import { SECTION_ORDER } from "./sections";
import { PhotosSection, ProceduresSection } from "./VisitSampleSections";
import { VisitSectionsLayout } from "./VisitSectionsLayout";

function allergyText(
  patient: Patient | null,
  locale: "th" | "en",
  t: (key: string) => string,
): string {
  if (!patient) return t("clinical.sourceUnavailable");
  if (patient.allergies.status === "none") return t("clinical.allergyNone");
  if (patient.allergies.status === "unknown")
    return t("clinical.allergyUnknown");
  return patient.allergies.items.map((item) => item[locale]).join(", ");
}

function FieldError({ message }: { message: string }) {
  return (
    <p className="pc-error" role="alert">
      {message}
    </p>
  );
}

/** Binding for the native encounter write loop (an `ENC-` read-model visit). */
export interface NativeVisitBinding {
  patientId: string;
  appointmentId?: string | null;
  encounter?: string | null;
  baseModified?: string | null;
  state?: "draft" | "completed";
}

function nativePayload(
  values: AuthoredVisitValues,
  rows: {
    labTests: NativeRow[];
    procedures: NativeRow[];
  },
) {
  const vitals: Record<string, string> = {};
  for (const key of ["temp", "pulse", "resp", "sys", "dia", "height", "weight"]) {
    const raw = (values as unknown as Record<string, string>)[key] ?? "";
    if (raw.trim() === "") continue;
    const mapping: Record<string, string> = {
      temp: "temperature",
      pulse: "pulse",
      resp: "respiratory_rate",
      sys: "bp_systolic",
      dia: "bp_diastolic",
      height: "height",
      weight: "weight",
    };
    vitals[mapping[key] as string] = raw.trim();
  }
  const diagnosis = values.diagnosisText.trim();
  const coded = (list: NativeRow[]) =>
    list.filter((row) => row.code.trim() !== "");
  return {
    complaint: values.complaint.trim() || null,
    diagnoses: diagnosis
      ? [{ diagnosis: values.diagnosisCode.trim() || diagnosis }]
      : [],
    // Medication entry is unavailable; the backend guards existing prescription rows.
    medications: [],
    lab_tests: coded(rows.labTests).map((row) => ({
      lab_test_code: row.code.trim(),
    })),
    procedures: coded(rows.procedures).map((row) => ({
      procedure: row.code.trim(),
    })),
    vitals,
    note: {
      assessment: values.assessment,
      plan: values.plan,
      rationale: values.rationale,
      followup: values.followup,
    },
    allergy_reviewed: values.allergyReviewed,
  };
}

interface NativeRow {
  code: string;
  second?: string;
}

/** One native prescription-row list with add/remove. Demo mode never renders it. */
function NativeRows({
  id,
  labelKey,
  secondLabelKey,
  addLabelKey,
  emptyKey,
  rows,
  onChange,
  disabled,
}: {
  id: string;
  labelKey: string;
  secondLabelKey?: string;
  addLabelKey: string;
  emptyKey: string;
  rows: NativeRow[];
  onChange: (rows: NativeRow[]) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const update = (index: number, patch: Partial<NativeRow>) =>
    onChange(rows.map((row, at) => (at === index ? { ...row, ...patch } : row)));
  return (
    <div className="pc-stack" data-testid={`native-rows-${id}`}>
      {rows.length === 0 && <p className="pc-note">{t(emptyKey)}</p>}
      {rows.map((row, index) => (
        <div className="pc-field-grid" key={`${id}-${index}`}>
          <div className="pc-field">
            <label className="pc-field__label" htmlFor={`${id}-${index}-code`}>
              {t(labelKey)}
            </label>
            <input
              id={`${id}-${index}-code`}
              value={row.code}
              onChange={(event) => update(index, { code: event.target.value })}
              disabled={disabled}
            />
          </div>
          {secondLabelKey !== undefined && (
            <div className="pc-field">
              <label
                className="pc-field__label"
                htmlFor={`${id}-${index}-second`}
              >
                {t(secondLabelKey)}
              </label>
              <input
                id={`${id}-${index}-second`}
                value={row.second ?? ""}
                onChange={(event) =>
                  update(index, { second: event.target.value })
                }
                disabled={disabled}
              />
            </div>
          )}
          <div className="pc-row">
            <Button
              variant="ghost"
              onClick={() => onChange(rows.filter((_row, at) => at !== index))}
              disabled={disabled}
            >
              {t("action.delete")}
            </Button>
          </div>
        </div>
      ))}
      <Button
        variant="secondary"
        onClick={() => onChange([...rows, { code: "" }])}
        disabled={disabled}
      >
        {t(addLabelKey)}
      </Button>
    </div>
  );
}

function writeFailureKey(code: string): string {
  switch (code) {
    case "stale":
      return "encounterWrite.stale";
    case "conflict":
      return "encounterWrite.conflict";
    case "validation":
      return "encounterWrite.invalid";
    case "forbidden":
    case "auth":
      return "encounterWrite.forbidden";
    case "locked":
      return "encounterWrite.locked";
    default:
      return "encounterWrite.uncertain";
  }
}

/**
 * Designed DOC-02 canonical editor.
 *
 * With an authored fixture `visit` it stays the local demo: save / review /
 * complete / amend are state transitions only and nothing is sent. With a
 * `native` binding (`ENC-` read-model visits) the same form drives the real
 * default-off encounter transport, but only where bootstrap enabled the exact
 * capability: draft (DOC/NUR vitals), complete and amend (DOC).
 */
export function AuthoredVisitWorkspace({
  visit,
  native,
}: {
  visit?: Visit;
  native?: NativeVisitBinding;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { get } = useCapabilities();
  const [params, setParams] = useSearchParams();
  const selected = params.get("section");
  const active: VisitSection =
    SECTION_ORDER.find((section) => section === selected) ?? "overview";
  const [values, setValues] = useState<AuthoredVisitValues>(() =>
    native ? emptyVisitValues() : initialVisitValues(visit as Visit, locale),
  );
  const [phase, setPhase] = useState<"draft" | "completed">(
    native ? (native.state ?? "draft") : (visit as Visit).state,
  );
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [addendum, setAddendum] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"complete" | "amend" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [encounterAlias, setEncounterAlias] = useState<string | null>(
    native?.encounter ?? null,
  );
  const [baseModified, setBaseModified] = useState<string | null>(
    native?.baseModified ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [labRows, setLabRows] = useState<NativeRow[]>([]);
  const [procedureRows, setProcedureRows] = useState<NativeRow[]>([]);

  const locked = phase === "completed";
  const readiness = readinessOf(values, phase);
  const patient = native ? null : coreData.patientById((visit as Visit).patientId);
  const practitioner = native
    ? (native.patientId ?? "—")
    : coreData.practitionerById((visit as Visit).practitioner).name[locale];
  const canDraft = get("write.encounter.draft") === "enabled";
  const canComplete = get("write.encounter.complete") === "enabled";
  const canAmend = get("write.encounter.amend") === "enabled";

  const change = (section: VisitSection) => {
    const next = new URLSearchParams(params);
    next.set("section", section);
    setParams(next, { replace: true });
  };

  const setValue =
    (key: keyof AuthoredVisitValues) =>
    (
      event: ChangeEvent<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >,
    ) => {
      const value =
        event.target instanceof HTMLInputElement &&
        event.target.type === "checkbox"
          ? event.target.checked
          : event.target.value;
      setValues((current) => ({ ...current, [key]: value }));
    };

  const save = () => {
    if (!native) {
      setSavedAt(new Date().toISOString());
      setError(null);
      return;
    }
    if (!canDraft) {
      setError(t("encounterWrite.forbidden"));
      return;
    }
    setBusy(true);
    setError(null);
    const operationKey = crypto.randomUUID();
    void saveNativeEncounterDraft({
      operation_key: operationKey,
      patient: native.patientId,
      encounter: encounterAlias,
      base_modified: baseModified,
      appointment: native.appointmentId ?? null,
      ...nativePayload(values, {
        labTests: labRows,
        procedures: procedureRows,
      }),
    })
      .then((result) => {
        setEncounterAlias(result.encounter.encounter_id);
        setBaseModified(result.encounter.modified);
        setSavedAt(new Date().toISOString());
      })
      .catch((failure: unknown) => {
        setError(
          t(
            writeFailureKey(
              failure instanceof NativeEncounterWriteError
                ? failure.code
                : "uncertain",
            ),
          ),
        );
      })
      .finally(() => setBusy(false));
  };

  const confirmCompletion = () => {
    const check = checkCompletion(values);
    if (!check.ok) {
      setError(
        check.reason === "required"
          ? t("visit.completeBlocked")
          : check.reason === "allergy"
            ? t("visit.completeBlocked")
            : t("visit.vitalsRange", {
                field: t(VITAL_FIELD_KEYS[check.detail ?? "temp"]),
              }),
      );
      setDialog(null);
      return;
    }
    setError(null);
    setDialog(null);
    if (!native) {
      setPhase("completed");
      setSavedAt(new Date().toISOString());
      return;
    }
    if (!canComplete) {
      setError(t("encounterWrite.forbidden"));
      return;
    }
    if (!encounterAlias) {
      setError(t("encounterWrite.saveFirst"));
      return;
    }
    setBusy(true);
    void completeNativeEncounter({
      operation_key: crypto.randomUUID(),
      encounter: encounterAlias,
      allergy_reviewed: values.allergyReviewed,
    })
      .then((result) => {
        setPhase("completed");
        setBaseModified(result.encounter.modified);
      })
      .catch((failure: unknown) => {
        setError(
          t(
            writeFailureKey(
              failure instanceof NativeEncounterWriteError
                ? failure.code
                : "uncertain",
            ),
          ),
        );
      })
      .finally(() => setBusy(false));
  };

  const submitAmendment = (text: string) => {
    setDialog(null);
    if (!native) {
      setAddendum(text);
      return;
    }
    if (!canAmend) {
      setError(t("encounterWrite.forbidden"));
      return;
    }
    if (!encounterAlias) {
      setError(t("encounterWrite.saveFirst"));
      return;
    }
    setBusy(true);
    void amendNativeEncounter({
      operation_key: crypto.randomUUID(),
      encounter: encounterAlias,
      addendum: text,
    })
      .then((result) => {
        setAddendum(result.addendum ?? text);
        setBaseModified(result.encounter.modified);
      })
      .catch((failure: unknown) => {
        setError(
          t(
            writeFailureKey(
              failure instanceof NativeEncounterWriteError
                ? failure.code
                : "uncertain",
            ),
          ),
        );
      })
      .finally(() => setBusy(false));
  };

  const numberField = (key: keyof typeof VITAL_RANGES) => (
    <div className="pc-field" key={key}>
      <label className="pc-field__label" htmlFor={`visit-${key}`}>
        {t(VITAL_FIELD_KEYS[key])}
      </label>
      <input
        id={`visit-${key}`}
        inputMode="decimal"
        type="number"
        value={values[key]}
        onChange={setValue(key)}
        disabled={locked}
        min={VITAL_RANGES[key][0]}
        max={VITAL_RANGES[key][1]}
      />
    </div>
  );

  const textArea = (
    key: Exclude<keyof AuthoredVisitValues, "allergyReviewed" | "primary">,
    labelKey: string,
    required = false,
  ) => (
    <div className="pc-field" key={key}>
      <label className="pc-field__label" htmlFor={`visit-${key}`}>
        {t(labelKey)}
      </label>
      <textarea
        id={`visit-${key}`}
        value={values[key]}
        onChange={setValue(key)}
        disabled={locked}
        aria-required={required || undefined}
      />
    </div>
  );

  const content = (section: VisitSection) => (
    <SectionFrame section={section}>
      {section === "overview" ? (
        <div className="pc-stack">
          <p className="clinical-notice">{t("visit.overviewNotice")}</p>
          <dl className="pc-dl">
            <div>
              <dt>{t("visit.careClinician")}</dt>
              <dd>{practitioner}</dd>
            </div>
            <div>
              <dt>{t("clinical.appointment")}</dt>
              <dd>{visit?.appointmentId ?? native?.appointmentId ?? "—"}</dd>
            </div>
            <div>
              <dt>{t("clinical.allergies")}</dt>
              <dd>{allergyText(patient, locale, t)}</dd>
            </div>
          </dl>
          <h3>{t("visit.previousHistory")}</h3>
          <p className="visit-readout">
            {visit?.history.past
              ? translateClinicalText(visit.history.past)
              : t("clinical.sourceUnavailable")}
          </p>
        </div>
      ) : section === "history" ? (
        <div className="pc-stack">
          {textArea("complaint", "clinical.complaint", true)}
          {textArea("hpi", "clinical.hpi", true)}
          {textArea("past", "clinical.past")}
          <div className="pc-field-grid">
            {textArea("surgical", "clinical.surgical")}
            {textArea("family", "clinical.family")}
          </div>
          <label className="pc-check">
            <input
              type="checkbox"
              checked={values.allergyReviewed}
              onChange={setValue("allergyReviewed")}
              disabled={locked}
            />
            <span>{t("visit.allergyReviewed")}</span>
          </label>
        </div>
      ) : section === "exam" ? (
        <div className="pc-stack">
          <div className="pc-field-grid">
            {(
              [
                "temp",
                "pulse",
                "resp",
                "sys",
                "dia",
                "height",
                "weight",
              ] as const
            ).map(numberField)}
            <div className="pc-field">
              <label className="pc-field__label" htmlFor="visit-examArea">
                {t("field.examArea")}
              </label>
              <input
                id="visit-examArea"
                value={values.examArea}
                onChange={setValue("examArea")}
                disabled={locked}
              />
            </div>
          </div>
          {textArea("examFindings", "field.examFindings", true)}
          <p className="pc-note">
            {t("visit.vitalsRecorded")}:{" "}
            {visit?.exam.vitals
              ? formatDateTime(visit.exam.vitals.recordedAt, locale)
              : t("clinical.notRecorded")}{" "}
            · {visit?.exam.vitals?.by ?? t("clinical.sourceUnavailable")}
          </p>
        </div>
      ) : section === "note" ? (
        <div className="pc-stack">
          {native && <p className="clinical-notice">{t("workspace.nativeNoteUnavailable")}</p>}
          <p className="pc-note">{t("visit.noteNotice")}</p>
          {textArea("assessment", "clinical.assessment", true)}
          {textArea("plan", "clinical.plan", true)}
          {textArea("rationale", "clinical.rationale")}
          {textArea("followup", "clinical.followup", true)}
        </div>
      ) : section === "diagnoses" ? (
        <div className="pc-stack">
          <div className="pc-field-grid">
            <div className="pc-field">
              <label className="pc-field__label" htmlFor="visit-diagnosisCode">
                {t("field.diagnosisCode")}
              </label>
              <input
                id="visit-diagnosisCode"
                value={values.diagnosisCode}
                onChange={setValue("diagnosisCode")}
                disabled={locked}
              />
            </div>
            <div className="pc-field">
              <label className="pc-field__label" htmlFor="visit-diagnosisText">
                {t("field.diagnosisText")}
              </label>
              <input
                id="visit-diagnosisText"
                value={values.diagnosisText}
                onChange={setValue("diagnosisText")}
                disabled={locked}
                required
              />
            </div>
          </div>
          <label className="pc-check">
            <input
              type="checkbox"
              checked={values.primary}
              onChange={setValue("primary")}
              disabled={locked}
            />
            <span>{t("visit.primaryDiagnosis")}</span>
          </label>
        </div>
      ) : section === "photos" ? (
        <>
          {native && <p className="clinical-notice">{t("workspace.nativePhotosUnavailable")}</p>}
          <PhotosSection patientId={native?.patientId ?? visit?.patientId ?? ""} />
        </>
      ) : section === "lab" ? (
        native ? (
          <div className="pc-stack">
            <p className="clinical-notice">{t("workspace.nativeLabUnavailable")}</p>
            <NativeRows
              id="native-lab"
              labelKey="encounterWrite.labTestCode"
              addLabelKey="action.addLab"
              emptyKey="visit.noLabs"
              rows={labRows}
              onChange={setLabRows}
              disabled={locked || !canDraft}
            />
            <p className="clinical-notice">{t("visit.medicationsNotice")}</p>
          </div>
        ) : (
          <ExternalLabSection patientId={visit?.patientId ?? ""} />
        )
      ) : section === "medications" ? (
        native ? (
          <p className="clinical-notice">{t("encounterWrite.medicationsUnavailable")}</p>
        ) : (
          <div className="pc-stack">
            {!visit || visit.meds.length === 0 ? (
              <p className="pc-note">{t("clinical.noMedications")}</p>
            ) : (
              <ul className="pc-lab-list">
                {visit.meds.map((med) => (
                  <li className="pc-listrow" key={med.item}>
                    <div className="pc-listrow__grow">
                      <strong>{translateClinicalText(med.item)}</strong>
                      <p className="pc-listrow__meta">
                        {translateClinicalText(med.dose)} ·{" "}
                        {translateClinicalText(med.freq)} ·{" "}
                        {translateClinicalText(med.duration)}
                      </p>
                    </div>
                    <Badge tone="info">
                      {med.status === "prescribed"
                        ? t("clinical.ordered")
                        : med.status}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="clinical-notice">{t("visit.medicationsNotice")}</p>
          </div>
        )
      ) : section === "procedures" ? (
        native ? (
          <div className="pc-stack">
            <NativeRows
              id="native-procedures"
              labelKey="encounterWrite.procedureCode"
              addLabelKey="action.addProcedure"
              emptyKey="visit.noProcedures"
              rows={procedureRows}
              onChange={setProcedureRows}
              disabled={locked || !canDraft}
            />
            <p className="clinical-notice">{t("visit.proceduresNotice")}</p>
          </div>
        ) : (
          <ProceduresSection patientId={visit?.patientId ?? ""} />
        )
      ) : section === "review" ? (
        <div className="pc-stack">
          <h3>{t("visit.reviewComposed")}</h3>
          <dl className="pc-dl">
            {(
              [
                ["complaint", "clinical.complaint"],
                ["hpi", "clinical.hpi"],
                ["examFindings", "clinical.findings"],
                ["assessment", "clinical.assessment"],
                ["plan", "clinical.plan"],
              ] as const
            ).map(([key, labelKey]) => (
              <div key={key}>
                <dt>{t(labelKey)}</dt>
                <dd>
                  {values[key].trim()
                    ? translateClinicalText(values[key])
                    : t("clinical.notRecorded")}
                </dd>
              </div>
            ))}
          </dl>
          {addendum && (
            <p className="pc-note" data-testid="visit-addendum">
              {t("visit.addendum", { text: addendum })}
            </p>
          )}
          <p className="pc-note">
            {native
              ? t("encounterWrite.reviewNote")
              : t("visit.sampleCompletionNote")}
          </p>
          {locked && (
            <div className="pc-row">
              <Button
                variant="secondary"
                onClick={() => setDialog("amend")}
                disabled={busy || (native && !canAmend) || addendum !== null}
              >
                {t("visit.amendAction")}
              </Button>
              <span className="pc-status-line">
                {addendum ? t("visit.amendKept") : ""}
              </span>
            </div>
          )}
        </div>
      ) : null}
    </SectionFrame>
  );

  return (
    <div
      className="visit-workspace"
      data-surface="DOC-02"
      data-testid={native ? "native-visit-workspace" : "sample-visit-workspace"}
      data-mode={native ? "native" : "demo"}
    >
      {native ? (
        <div className="pc-banner pc-banner--demo" data-testid="native-write-banner">
          <div className="pc-banner__body">
            <div className="pc-banner__title">{t("encounterWrite.title")}</div>
            <p className="pc-note">{t("encounterWrite.notice")}</p>
          </div>
        </div>
      ) : (
        <SampleBanner label={t("app.demoTag")} title={t("visit.sampleBanner")}>
          <p className="pc-note">{t("visit.sampleNoWrite")}</p>
        </SampleBanner>
      )}

      <header className="visit-identity">
        <div className="visit-identity-heading">
          <span className="visit-avatar" aria-hidden="true">
            ✚
          </span>
          <div>
            <p>{t("clinical.patient")}</p>
            <h2>
              {native
                ? native.patientId
                : coreData.patientName((visit as Visit).patientId, locale)}
            </h2>
          </div>
          <span className="visit-readonly">
            {locked
              ? t("visit.readinessLocked")
              : native
                ? t("encounterWrite.draftTag")
                : t("app.demoTag")}
          </span>
        </div>
        <dl>
          <div>
            <dt>{t("clinical.encounterId")}</dt>
            <dd data-testid="encounter-id">
              {native ? (encounterAlias ?? "—") : (visit as Visit).id}
            </dd>
          </div>
          <div>
            <dt>{t("clinical.patientId")}</dt>
            <dd>{native ? native.patientId : (visit as Visit).patientId}</dd>
          </div>
          <div>
            <dt>{t("clinical.started")}</dt>
            <dd>
              {native
                ? savedAt
                  ? formatDateTime(savedAt, locale)
                  : "—"
                : formatDateTime((visit as Visit).date, locale)}
            </dd>
          </div>
          <div>
            <dt>{t("clinical.appointment")}</dt>
            <dd>
              {native
                ? (native.appointmentId ?? "—")
                : ((visit as Visit).appointmentId ?? "—")}
            </dd>
          </div>
          <div>
            <dt>{t("clinical.practitioner")}</dt>
            <dd>{practitioner}</dd>
          </div>
          <div>
            <dt>{t("clinical.state")}</dt>
            <dd>
              <Badge tone={locked ? "success" : "warning"}>
                {t(locked ? "status.completed" : "status.draft")}
              </Badge>
            </dd>
          </div>
        </dl>
      </header>

      <div
        id="visitReadiness"
        className={`pc-banner ${locked ? "pc-banner--demo" : readiness.state === "ready" ? "pc-banner--demo" : "pc-banner--warn"}`}
        role="status"
      >
        <div className="pc-banner__body">
          <div className="pc-banner__title">{t("visit.readiness")}</div>
          {readiness.state === "locked"
            ? t("visit.readinessLocked")
            : readiness.state === "ready"
              ? t("visit.readinessReady")
              : t("visit.readinessMissing", {
                  fields: readiness.missing.map((key) => t(key)).join(" · "),
                })}
        </div>
      </div>

      <VisitSectionsLayout active={active} onChange={change} render={content} />

      {error && <FieldError message={error} />}

      <div className="visit-save-footer">
        <p className="pc-status-line" data-testid="visit-save-status">
          {locked
            ? t("visit.readinessLocked")
            : savedAt
              ? native
                ? t("encounterWrite.saved", {
                    time: formatDateTime(savedAt, locale),
                  })
                : t("visit.draftKept", {
                    time: formatDateTime(savedAt, locale),
                  })
              : native
                ? t("encounterWrite.unsaved")
                : `${t("visit.unsaved")} · ${t("visit.sampleNoWrite")}`}
        </p>
        <div className="pc-row">
          <Button
            variant="secondary"
            onClick={save}
            disabled={locked || busy || (native && !canDraft)}
          >
            {t("action.saveDraft")}
          </Button>
          <Button variant="secondary" onClick={() => change("review")}>
            {t("action.review")}
          </Button>
          <Button
            variant="primary"
            onClick={() => setDialog("complete")}
            disabled={locked || busy || (native && !canComplete)}
          >
            {t("visit.completeAction")}
          </Button>
        </div>
      </div>

      {dialog === "complete" && (
        <Drawer
          title={t("visit.completeTitle")}
          onClose={() => setDialog(null)}
          footer={
            <div className="pc-row">
              <Button variant="ghost" onClick={() => setDialog(null)}>
                {t("action.close")}
              </Button>
              <Button variant="primary" onClick={confirmCompletion}>
                {t("action.confirm")}
              </Button>
            </div>
          }
        >
          <div className="pc-stack">
            <p>{native ? t("encounterWrite.completeBody") : t("visit.completeBody")}</p>
            {outOfRangeVitals(values).length > 0 && (
              <p className="pc-error">
                {t("visit.vitalsRange", {
                  field: t(
                    VITAL_FIELD_KEYS[outOfRangeVitals(values)[0] ?? "temp"],
                  ),
                })}
              </p>
            )}
            {!native && <p className="pc-note">{t("visit.sampleNoWrite")}</p>}
          </div>
        </Drawer>
      )}

      {dialog === "amend" && (
        <AmendmentDrawer
          onClose={() => setDialog(null)}
          onConfirm={submitAmendment}
        />
      )}
    </div>
  );
}

function AmendmentDrawer({
  onClose,
  onConfirm,
}: {
  onClose: () => void;
  onConfirm: (text: string) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const empty = text.trim().length === 0;
  return (
    <Drawer
      title={t("visit.amendTitle")}
      onClose={onClose}
      footer={
        <div className="pc-row">
          <Button variant="ghost" onClick={onClose}>
            {t("action.close")}
          </Button>
          <Button
            variant="primary"
            onClick={() => onConfirm(text.trim())}
            disabled={empty}
          >
            {t("action.confirm")}
          </Button>
        </div>
      }
    >
      <div className="pc-stack">
        <div className="pc-field">
          <label className="pc-field__label" htmlFor="visit-amendReason">
            {t("visit.amendReason")}
          </label>
          <textarea
            id="visit-amendReason"
            value={text}
            onChange={(event) => setText(event.target.value)}
            required
          />
        </div>
        <p className="pc-note">
          {empty ? t("visit.amendRequired") : t("visit.amendKept")}
        </p>
      </div>
    </Drawer>
  );
}
