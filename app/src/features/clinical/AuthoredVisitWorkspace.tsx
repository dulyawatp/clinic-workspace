import { type ChangeEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Drawer, SampleBanner } from "../../design/components";
import type { VisitSection } from "../../domain/clinical";
import { coreData, type Patient, type Visit } from "../../fixtures/data";
import { translateClinicalText } from "../../i18n";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import {
  type AuthoredVisitValues,
  checkCompletion,
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

/**
 * Designed DOC-02 canonical editor over the authored sample encounter.
 *
 * Save / review / complete / amend are demo state transitions only: nothing is
 * sent anywhere, there is no signature, order, stock movement or invoice, and
 * the surface says so above and beside the controls.
 */
export function AuthoredVisitWorkspace({ visit }: { visit: Visit }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const [params, setParams] = useSearchParams();
  const selected = params.get("section");
  const active: VisitSection =
    SECTION_ORDER.find((section) => section === selected) ?? "overview";
  const [values, setValues] = useState<AuthoredVisitValues>(() =>
    initialVisitValues(visit, locale),
  );
  const [phase, setPhase] = useState<"draft" | "completed">(visit.state);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [addendum, setAddendum] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"complete" | "amend" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const locked = phase === "completed";
  const readiness = readinessOf(values, phase);
  const patient = coreData.patientById(visit.patientId);
  const practitioner = coreData.practitionerById(visit.practitioner).name[
    locale
  ];

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
    setSavedAt(new Date().toISOString());
    setError(null);
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
    setPhase("completed");
    setSavedAt(new Date().toISOString());
    setDialog(null);
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
              <dd>{visit.appointmentId ?? "—"}</dd>
            </div>
            <div>
              <dt>{t("clinical.allergies")}</dt>
              <dd>{allergyText(patient, locale, t)}</dd>
            </div>
          </dl>
          <h3>{t("visit.previousHistory")}</h3>
          <p className="visit-readout">
            {visit.history.past
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
            {visit.exam.vitals
              ? formatDateTime(visit.exam.vitals.recordedAt, locale)
              : t("clinical.notRecorded")}{" "}
            · {visit.exam.vitals?.by ?? t("clinical.sourceUnavailable")}
          </p>
        </div>
      ) : section === "note" ? (
        <div className="pc-stack">
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
        <PhotosSection patientId={visit.patientId} />
      ) : section === "lab" ? (
        <ExternalLabSection patientId={visit.patientId} />
      ) : section === "medications" ? (
        <div className="pc-stack">
          {visit.meds.length === 0 ? (
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
      ) : section === "procedures" ? (
        <ProceduresSection patientId={visit.patientId} />
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
          <p className="pc-note">{t("visit.sampleCompletionNote")}</p>
          {locked && (
            <div className="pc-row">
              <Button
                variant="secondary"
                onClick={() => setDialog("amend")}
                disabled={locked && addendum !== null}
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
      data-testid="sample-visit-workspace"
    >
      <SampleBanner label={t("app.demoTag")} title={t("visit.sampleBanner")}>
        <p className="pc-note">{t("visit.sampleNoWrite")}</p>
      </SampleBanner>

      <header className="visit-identity">
        <div className="visit-identity-heading">
          <span className="visit-avatar" aria-hidden="true">
            ✚
          </span>
          <div>
            <p>{t("clinical.patient")}</p>
            <h2>{coreData.patientName(visit.patientId, locale)}</h2>
          </div>
          <span className="visit-readonly">
            {locked ? t("visit.readinessLocked") : t("app.demoTag")}
          </span>
        </div>
        <dl>
          <div>
            <dt>{t("clinical.encounterId")}</dt>
            <dd data-testid="encounter-id">{visit.id}</dd>
          </div>
          <div>
            <dt>{t("clinical.patientId")}</dt>
            <dd>{visit.patientId}</dd>
          </div>
          <div>
            <dt>{t("clinical.started")}</dt>
            <dd>{formatDateTime(visit.date, locale)}</dd>
          </div>
          <div>
            <dt>{t("clinical.appointment")}</dt>
            <dd>{visit.appointmentId ?? "—"}</dd>
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
              ? t("visit.draftKept", {
                  time: formatDateTime(savedAt, locale),
                })
              : `${t("visit.unsaved")} · ${t("visit.sampleNoWrite")}`}
        </p>
        <div className="pc-row">
          <Button variant="secondary" onClick={save} disabled={locked}>
            {t("action.saveDraft")}
          </Button>
          <Button variant="secondary" onClick={() => change("review")}>
            {t("action.review")}
          </Button>
          <Button
            variant="primary"
            onClick={() => setDialog("complete")}
            disabled={locked}
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
            <p>{t("visit.completeBody")}</p>
            {outOfRangeVitals(values).length > 0 && (
              <p className="pc-error">
                {t("visit.vitalsRange", {
                  field: t(
                    VITAL_FIELD_KEYS[outOfRangeVitals(values)[0] ?? "temp"],
                  ),
                })}
              </p>
            )}
            <p className="pc-note">{t("visit.sampleNoWrite")}</p>
          </div>
        </Drawer>
      )}

      {dialog === "amend" && (
        <AmendmentDrawer
          onClose={() => setDialog(null)}
          onConfirm={(text) => {
            setAddendum(text);
            setDialog(null);
          }}
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
