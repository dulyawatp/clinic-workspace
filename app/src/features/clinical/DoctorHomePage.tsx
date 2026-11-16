import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  type BadgeTone,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { type AppointmentStatus, coreData } from "../../fixtures/data";
import { recentEncountersFor } from "../../fixtures/data-clinical";
import { opsData } from "../../fixtures/data-ops";
import { translateClinicalText } from "../../i18n";
import { formatDate, formatDateTime, formatTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import {
  draftVisitsFor,
  followUpVisitsFor,
  patientLabel,
  practitionerLabel,
  receivedLabsFor,
  roomLabel,
  SAMPLE_DOCTOR_PRACTITIONER,
  SAMPLE_DOCTOR_STAFF,
  sampleDoctorName,
  sampleStaffName,
} from "./sample-scope";

const APPOINTMENT_TONES: Record<AppointmentStatus, BadgeTone> = {
  scheduled: "neutral",
  confirmed: "info",
  arrived: "info",
  checkedIn: "info",
  waiting: "warning",
  withDoctor: "success",
  completed: "success",
  cancelled: "danger",
  noShow: "danger",
};

/**
 * DOC-01 My Day. The prototype reads one practitioner's day from the demo
 * store; this surface recomputes the four work counts from the same sample
 * rows instead of copying a stored total, and says out loud which sample
 * clinician the role-only demo account maps to.
 */
export function DoctorHomePage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const practitioner = SAMPLE_DOCTOR_PRACTITIONER;
  const today = coreData.dates.todayKey();
  const rows = coreData
    .appointmentsOn(today)
    .filter((appointment) => appointment.practitioner === practitioner);
  const next =
    rows.find((appointment) => appointment.status === "withDoctor") ??
    rows.find((appointment) =>
      ["waiting", "checkedIn", "arrived"].includes(appointment.status),
    );
  const drafts = draftVisitsFor(practitioner);
  const labs = receivedLabsFor(practitioner);
  const followUps = followUpVisitsFor(practitioner);
  const schedules = opsData.schedules.filter(
    (schedule) => schedule.practitioner === practitioner,
  );
  const hours = opsData.hours();
  const breaks = hours.breaks.filter(
    (row) => row.practitioner === "all" || row.practitioner === practitioner,
  );
  const leave = hours.leave.filter(
    (row) => row.practitioner === "all" || row.practitioner === practitioner,
  );
  const recent = recentEncountersFor(practitioner, 5);
  const firstLab = labs[0];

  const counts = [
    {
      key: "appointmentsToday",
      value: rows.length,
      href: "#doctorMyDay",
    },
    { key: "openDrafts", value: drafts.length, href: "#doctorDrafts" },
    {
      key: "labsAwaiting",
      value: labs.length,
      href: firstLab ? ROUTES.lab(firstLab.id) : "#doctorMyDay",
    },
    { key: "followUps", value: followUps.length, href: "#doctorFollowUps" },
  ] as const;

  return (
    <div data-surface="DOC-01" data-testid="doctor-home">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("surface.DOC-01")}
      />
      <div className="pc-page__body">
        <SampleBanner label={t("app.demoTag")} title={t("doctor.banner")}>
          <p className="pc-note">
            {t("doctor.samplePractitioner", {
              name: sampleDoctorName(locale),
            })}{" "}
            · {sampleStaffName(SAMPLE_DOCTOR_STAFF, locale)} ·{" "}
            {t("doctor.sampleIdentity")}
          </p>
        </SampleBanner>

        <Card title={t("doctor.nextPatient")}>
          {next ? (
            <div className="pc-stack">
              <div>
                <h3 className="doctor-next-name">
                  {patientLabel(next.patientId, locale)}
                </h3>
                <p className="pc-status-line">
                  {formatTime(next.date, locale)} ·{" "}
                  <Badge tone={APPOINTMENT_TONES[next.status]}>
                    {t(`status.${next.status}`)}
                  </Badge>{" "}
                  · {next.id}
                </p>
              </div>
              {next.visitId ? (
                <div className="pc-row">
                  <Link
                    className="pc-button pc-button--primary"
                    to={ROUTES.visit(next.visitId)}
                  >
                    {t("doctor.nextDraft")}
                  </Link>
                  <span className="pc-note">
                    {t("clinical.encounterId")}: {next.visitId}
                  </span>
                </div>
              ) : (
                <p className="pc-note">{t("visit.noEncounterYet")}</p>
              )}
            </div>
          ) : (
            <p className="pc-note">{t("doctor.noPatientReady")}</p>
          )}
        </Card>

        <section className="pc-kpis" aria-label={t("doctor.myDay")}>
          {counts.map((count) => (
            <a
              className="pc-kpi"
              data-testid={`kpi-${count.key}`}
              href={count.href}
              key={count.key}
            >
              <span className="pc-kpi__label">
                {t(`doctor.kpi.${count.key}`)}
              </span>
              <strong className="pc-kpi__value">{count.value}</strong>
            </a>
          ))}
        </section>

        <Card title={t("doctor.myDay")} hint={t("doctor.myDayHint")} flush>
          <div id="doctorMyDay">
            <DataTable
              caption={t("doctor.myDay")}
              columns={[
                { key: "time", label: t("doctor.time") },
                { key: "patient", label: t("appointment.patient") },
                { key: "type", label: t("doctor.appointmentType") },
                { key: "status", label: t("doctor.status") },
                { key: "action", label: "" },
              ]}
              rows={rows.map((appointment) => [
                formatTime(appointment.date, locale),
                <span key="patient">
                  {patientLabel(appointment.patientId, locale)}
                  <br />
                  <span className="pc-note">{appointment.patientId}</span>
                </span>,
                t(coreData.appointmentTypeById(appointment.typeId).key),
                <Badge
                  key="status"
                  tone={APPOINTMENT_TONES[appointment.status]}
                >
                  {t(`status.${appointment.status}`)}
                </Badge>,
                appointment.visitId ? (
                  <Link
                    key="action"
                    className="pc-button pc-button--secondary pc-button--sm"
                    to={ROUTES.visit(appointment.visitId)}
                  >
                    {t("doctor.openVisit")}
                  </Link>
                ) : (
                  <span className="pc-note" key="action">
                    {t("visit.noEncounterYet")}
                  </span>
                ),
              ])}
              empty={t("appointment.emptyDay")}
            />
          </div>
        </Card>

        <div className="doctor-columns">
          <Card title={t("doctor.openDrafts")}>
            <div id="doctorDrafts">
              {drafts.length === 0 ? (
                <p className="pc-note">{t("doctor.noDrafts")}</p>
              ) : (
                drafts.map((draft) => (
                  <div className="pc-listrow" key={draft.id}>
                    <div className="pc-listrow__grow">
                      <Link to={ROUTES.visit(draft.id)}>
                        {t("doctor.openDraft", { id: draft.id })}
                      </Link>
                      <p className="pc-listrow__meta">
                        {patientLabel(draft.patientId, locale)} ·{" "}
                        {draft.draftSavedAt
                          ? formatDateTime(draft.draftSavedAt, locale)
                          : t("clinical.notRecorded")}
                      </p>
                    </div>
                    <Badge tone="warning">{t("status.draft")}</Badge>
                  </div>
                ))
              )}
            </div>
          </Card>

          <Card title={t("doctor.followUps")}>
            <div id="doctorFollowUps">
              {followUps.length === 0 ? (
                <p className="pc-note">{t("doctor.noFollowUps")}</p>
              ) : (
                followUps.map((visit) => (
                  <div className="pc-listrow" key={visit.id}>
                    <div className="pc-listrow__grow">
                      {patientLabel(visit.patientId, locale)}
                      <p className="pc-listrow__meta">
                        {translateClinicalText(visit.note.followup ?? "")}
                      </p>
                    </div>
                    <Link
                      className="pc-button pc-button--secondary pc-button--sm"
                      to={ROUTES.visit(visit.id)}
                    >
                      {t("doctor.openEncounter")}
                    </Link>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>

        <Card title={t("doctor.hours")} hint={t("doctor.hoursNote")}>
          <p className="pc-status-line">
            {t("clinical.practitioner")}:{" "}
            {practitionerLabel(practitioner, locale)} · {practitioner} ·{" "}
            {hours.timezone}
          </p>
          {schedules.map((schedule) => (
            <p
              className="pc-status-line"
              key={`${schedule.day}-${schedule.start}`}
            >
              {t(`day.${schedule.day}`)} · {schedule.start}–{schedule.end} ·{" "}
              {roomLabel(schedule.room, locale)}
            </p>
          ))}
          {breaks.length > 0 && (
            <>
              <p className="pc-field__label">{t("doctor.breaksLeave")}</p>
              {breaks.map((row) => (
                <p
                  className="pc-status-line"
                  key={`break-${row.practitioner}-${row.day}-${row.start}`}
                >
                  {t(`day.${row.day}`)} {row.start}–{row.end}
                </p>
              ))}
            </>
          )}
          {leave.map((row) => (
            <p
              className="pc-status-line"
              key={`leave-${row.practitioner}-${row.date}-${row.start}`}
            >
              {formatDate(row.date, locale)} {row.start}–{row.end}
            </p>
          ))}
        </Card>

        <Card title={t("doctor.recentEncounters")}>
          {recent.length === 0 ? (
            <p className="pc-note">{t("doctor.recentEmpty")}</p>
          ) : (
            recent.map((visit) => (
              <div className="pc-listrow" key={visit.id}>
                <div className="pc-listrow__grow">
                  {patientLabel(visit.patientId, locale)}
                  <p className="pc-listrow__meta">
                    {formatDateTime(visit.date, locale)} ·{" "}
                    {visit.diagnoses[0]
                      ? translateClinicalText(visit.diagnoses[0].label[locale])
                      : visit.history.complaintKey
                        ? t(visit.history.complaintKey)
                        : visit.history.complaint
                          ? translateClinicalText(visit.history.complaint)
                          : t("clinical.sourceUnavailable")}
                  </p>
                </div>
                <Badge tone={visit.state === "draft" ? "warning" : "success"}>
                  {t(
                    visit.state === "draft"
                      ? "status.draft"
                      : "status.completed",
                  )}
                </Badge>
                <Link
                  className="pc-button pc-button--secondary pc-button--sm"
                  to={ROUTES.visit(visit.id)}
                >
                  {t("doctor.openEncounter")}
                </Link>
              </div>
            ))
          )}
        </Card>
      </div>
    </div>
  );
}
