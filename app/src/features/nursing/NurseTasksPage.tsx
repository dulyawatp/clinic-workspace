import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Button,
  Card,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import type { NurseAssignment } from "../../fixtures/data-clinical";
import { translateClinicalText } from "../../i18n";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { LabStatusBadge } from "../clinical/LabPanels";
import {
  labRequestsForPatient,
  nurseAssignmentFor,
  nurseCohortDefault,
  nurseContextValid,
  patientLabel,
  practitionerLabel,
} from "../clinical/sample-scope";

type TaskTab = "vitals" | "prep" | "aftercare" | "lab";

const TABS: readonly TaskTab[] = ["vitals", "prep", "aftercare", "lab"];

const VITAL_LIMITS: Readonly<Record<string, readonly [number, number]>> = {
  temperature: [30, 43],
  pulse: [25, 250],
  systolic: [50, 250],
  diastolic: [30, 160],
  respiration: [5, 60],
  height: [40, 250],
  weight: [2, 350],
};

/** Bangkok wall-clock value for a `datetime-local` input. */
function localDateTimeInput(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Bangkok",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const part = (type: string) =>
    parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

const VITAL_LABELS: Readonly<Record<string, string>> = {
  temperature: "field.temperature",
  pulse: "field.pulse",
  systolic: "field.systolic",
  diastolic: "field.diastolic",
  respiration: "field.respiration",
  height: "field.height",
  weight: "field.weight",
};

function tabOf(value: string | null): TaskTab {
  return TABS.find((tab) => tab === value) ?? "vitals";
}

/**
 * NUR-02 nursing tasks. Vitals, preparation, aftercare and lab dispatch are
 * designed forms over the authored assignment row; every save is a local demo
 * acknowledgement and says that nothing is sent to a server.
 */
export function NurseTasksPage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params.get("tab"));
  const fallback = nurseCohortDefault();
  const patientParam = params.get("patient");
  const appointmentParam = params.get("appointment");
  const patientId = patientParam ?? fallback?.patientId ?? null;
  const appointmentId = patientParam
    ? appointmentParam
    : (fallback?.appointmentId ?? null);
  const coherent =
    patientId !== null && nurseContextValid(patientId, appointmentId);
  const assignment =
    patientId === null ? null : nurseAssignmentFor(patientId, appointmentId);

  const changeTab = (next: TaskTab) => {
    const query = new URLSearchParams(params);
    query.set("tab", next);
    setParams(query, { replace: true });
  };

  if (!patientId || !coherent || !assignment) {
    return (
      <div data-surface="NUR-02" data-testid="nurse-tasks">
        <PageHeader
          breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
          eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
          title={t("surface.NUR-02")}
        />
        <div className="pc-page__body">
          <Card>
            <p className="pc-error" role="alert">
              {t("nurse.mismatch")}
            </p>
            <p className="pc-note">{t("nurse.assignmentNote")}</p>
            {fallback && (
              <Link
                className="pc-button pc-button--secondary"
                to={`${ROUTES.nurseTasks}?patient=${encodeURIComponent(fallback.patientId)}&appointment=${encodeURIComponent(fallback.appointmentId)}&tab=vitals`}
              >
                {t("nurse.openTask")}
              </Link>
            )}
          </Card>
        </div>
      </div>
    );
  }

  const patientName = patientLabel(assignment.patientId, locale);

  return (
    <div data-surface="NUR-02" data-testid="nurse-tasks">
      <PageHeader
        breadcrumb={<Link to={ROUTES.nurse}>{t("surface.NUR-01")}</Link>}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("surface.NUR-02")}
      />
      <div className="pc-page__body">
        <SampleBanner label={t("app.demoTag")} title={t("nurse.banner")}>
          <p className="pc-note">{t("nurse.noWrite")}</p>
        </SampleBanner>

        <Card title={patientName} hint={t("nurse.taskTitle")}>
          <dl className="pc-dl">
            <div>
              <dt>{t("nurse.patient")}</dt>
              <dd>
                {patientName} · {assignment.patientId}
              </dd>
            </div>
            <div>
              <dt>{t("nurse.practitioner")}</dt>
              <dd>{practitionerLabel(assignment.practitioner, locale)}</dd>
            </div>
            <div>
              <dt>{t("appointment.detail")}</dt>
              <dd>{assignment.appointmentId}</dd>
            </div>
          </dl>
          <p className="pc-note" data-testid="nurse-task-context">
            {t("nurse.taskContext")}
            {assignment.vitals
              ? ` · ${t("nurse.measured")}: ${formatDateTime(assignment.vitals.measuredAt, locale)}`
              : ""}
          </p>
        </Card>

        <nav className="nurse-tabs" aria-label={t("surface.NUR-02")}>
          {TABS.map((entry) => (
            <button
              key={entry}
              type="button"
              className="pc-button pc-button--secondary pc-button--sm"
              aria-pressed={tab === entry}
              onClick={() => changeTab(entry)}
            >
              {t(`nurse.tab.${entry}`)}
            </button>
          ))}
        </nav>

        <Card title={t(`nurse.tab.${tab}`)}>
          {tab === "vitals" ? (
            <VitalsForm assignment={assignment} />
          ) : tab === "prep" ? (
            <PrepForm assignment={assignment} />
          ) : tab === "aftercare" ? (
            <AftercareForm assignment={assignment} />
          ) : (
            <LabDispatch patientId={assignment.patientId} />
          )}
        </Card>
      </div>
    </div>
  );
}

function SavedNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="pc-status-line" role="status" aria-live="polite">
      {message}
    </p>
  );
}

function VitalsForm({ assignment }: { assignment: NurseAssignment }) {
  const { t } = useTranslation();
  const [values, setValues] = useState({
    measuredAt: assignment.vitals
      ? localDateTimeInput(assignment.vitals.measuredAt)
      : "",
    temperature: assignment.vitals ? String(assignment.vitals.temperature) : "",
    pulse: assignment.vitals ? String(assignment.vitals.pulse) : "",
    systolic: assignment.vitals ? String(assignment.vitals.systolic) : "",
    diastolic: assignment.vitals ? String(assignment.vitals.diastolic) : "",
    respiration: assignment.vitals ? String(assignment.vitals.respiration) : "",
    height: assignment.vitals ? String(assignment.vitals.height) : "",
    weight: assignment.vitals ? String(assignment.vitals.weight) : "",
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(null);
    if (
      ["measuredAt", "temperature", "pulse", "systolic", "diastolic"].some(
        (key) => values[key as keyof typeof values].trim() === "",
      )
    ) {
      setError(t("nurse.taskRequired"));
      return;
    }
    for (const [key, [min, max]] of Object.entries(VITAL_LIMITS)) {
      const raw = values[key as keyof typeof values];
      if (raw.trim() === "") continue;
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
        setError(t("nurse.rangeError", { field: t(VITAL_LABELS[key] ?? key) }));
        return;
      }
    }
    setError(null);
    setSaved(t("nurse.savedLocal"));
  };

  return (
    <form className="pc-stack" onSubmit={submit} noValidate>
      <div className="pc-field-grid">
        <div className="pc-field">
          <label className="pc-field__label" htmlFor="nurse-measuredAt">
            {t("field.measuredAt")}
          </label>
          <input
            id="nurse-measuredAt"
            type="datetime-local"
            value={values.measuredAt}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                measuredAt: event.target.value,
              }))
            }
            required
          />
        </div>
        {Object.keys(VITAL_LIMITS).map((key) => (
          <div className="pc-field" key={key}>
            <label className="pc-field__label" htmlFor={`nurse-${key}`}>
              {t(VITAL_LABELS[key] ?? key)}
            </label>
            <input
              id={`nurse-${key}`}
              type="number"
              inputMode="decimal"
              min={VITAL_LIMITS[key]?.[0]}
              max={VITAL_LIMITS[key]?.[1]}
              value={values[key as keyof typeof values]}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  [key]: event.target.value,
                }))
              }
            />
          </div>
        ))}
      </div>
      {error && (
        <p className="pc-error" role="alert">
          {error}
        </p>
      )}
      <SavedNote message={saved} />
      <div className="pc-row">
        <Button type="submit" variant="primary">
          {t("nurse.saveVitals")}
        </Button>
      </div>
    </form>
  );
}

function PrepForm({ assignment }: { assignment: NurseAssignment }) {
  const { t } = useTranslation();
  const [checks, setChecks] = useState({
    identity: assignment.prepChecklist.identity,
    consent: assignment.prepChecklist.consent,
    room: assignment.prepChecklist.room,
    supplies: assignment.prepChecklist.supplies,
  });
  const [note, setNote] = useState(assignment.prepChecklist.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(null);
    if (!Object.values(checks).every(Boolean)) {
      setError(t("nurse.prepRequired"));
      return;
    }
    setError(null);
    setSaved(t("nurse.savedLocal"));
  };

  return (
    <form className="pc-stack" onSubmit={submit} noValidate>
      {(Object.keys(checks) as Array<keyof typeof checks>).map((key) => (
        <label className="pc-check" key={key}>
          <input
            type="checkbox"
            checked={checks[key]}
            onChange={(event) =>
              setChecks((current) => ({
                ...current,
                [key]: event.target.checked,
              }))
            }
          />
          <span>{t(`nurse.prep.${key}`)}</span>
        </label>
      ))}
      <div className="pc-field">
        <label className="pc-field__label" htmlFor="nurse-prepNote">
          {t("nurse.prepNote")}
        </label>
        <textarea
          id="nurse-prepNote"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
      {error && (
        <p className="pc-error" role="alert">
          {error}
        </p>
      )}
      <SavedNote message={saved} />
      <div className="pc-row">
        <Button type="submit" variant="primary">
          {t("nurse.savePrep")}
        </Button>
      </div>
    </form>
  );
}

function AftercareForm({ assignment }: { assignment: NurseAssignment }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const [values, setValues] = useState({
    instructions: assignment.aftercare.instructions?.[locale] ?? "",
    followupDate: assignment.aftercare.followupDate ?? "",
    understood: assignment.aftercare.acknowledged,
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(null);
    if (
      values.instructions.trim() === "" ||
      values.followupDate.trim() === ""
    ) {
      setError(t("nurse.taskRequired"));
      return;
    }
    if (!values.understood) {
      setError(t("nurse.aftercareRequired"));
      return;
    }
    setError(null);
    setSaved(t("nurse.savedLocal"));
  };

  return (
    <form className="pc-stack" onSubmit={submit} noValidate>
      <div className="pc-field">
        <label className="pc-field__label" htmlFor="nurse-aftercare">
          {t("nurse.aftercareInstructions")}
        </label>
        <textarea
          id="nurse-aftercare"
          value={values.instructions}
          onChange={(event) =>
            setValues((current) => ({
              ...current,
              instructions: event.target.value,
            }))
          }
          required
        />
      </div>
      <div className="pc-field">
        <label className="pc-field__label" htmlFor="nurse-followupDate">
          {t("nurse.followupDate")}
        </label>
        <input
          id="nurse-followupDate"
          type="date"
          value={values.followupDate}
          onChange={(event) =>
            setValues((current) => ({
              ...current,
              followupDate: event.target.value,
            }))
          }
          required
        />
      </div>
      <label className="pc-check">
        <input
          type="checkbox"
          checked={values.understood}
          onChange={(event) =>
            setValues((current) => ({
              ...current,
              understood: event.target.checked,
            }))
          }
        />
        <span>{t("nurse.understood")}</span>
      </label>
      {error && (
        <p className="pc-error" role="alert">
          {error}
        </p>
      )}
      <SavedNote message={saved} />
      <div className="pc-row">
        <Button type="submit" variant="primary">
          {t("nurse.saveAftercare")}
        </Button>
      </div>
    </form>
  );
}

function LabDispatch({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  // The prototype's dispatch tab lists only work still in the queue: an order
  // waiting to go out or one sent and awaiting its result. Collected, received
  // and reviewed rows are not dispatch work.
  const rows = labRequestsForPatient(patientId).filter(
    (row) => row.status === "ordered" || row.status === "sent",
  );
  return (
    <div className="pc-stack">
      {rows.length === 0 ? (
        <p className="pc-note">{t("nurse.labEmpty")}</p>
      ) : (
        rows.map((row) => (
          <div className="pc-listrow" key={row.id}>
            <div className="pc-listrow__grow">
              <strong>{translateClinicalText(row.test[locale])}</strong>
              <p className="pc-listrow__meta">
                {row.id} · {row.provider} ·{" "}
                {row.sentAt
                  ? formatDateTime(row.sentAt, locale)
                  : t("lab.refPending")}
              </p>
            </div>
            <LabStatusBadge status={row.status} />
            <Link
              className="pc-button pc-button--secondary pc-button--sm"
              to={ROUTES.lab(row.id)}
            >
              {t("nurse.openHandoff")}
            </Link>
          </div>
        ))
      )}
      <p className="pc-note">{t("nurse.labNote")}</p>
      <Badge tone="warning">{t("lab.modelUnverified")}</Badge>
    </div>
  );
}
