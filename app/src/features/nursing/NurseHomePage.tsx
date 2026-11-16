import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import {
  nurseDutyTiles,
  upcomingProcedures,
} from "../../fixtures/data-clinical";
import { formatDateTime, formatTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { SurfaceIcon } from "../clinical/SurfaceIcon";
import {
  nurseCohort,
  patientLabel,
  practitionerLabel,
  roomLabel,
  SAMPLE_NURSE_STAFF,
  sampleStaffName,
  templateName,
} from "../clinical/sample-scope";

/**
 * NUR-01 assigned work. The cohort is the authored sample assignment list, not
 * a server assignment; the screen says so and keeps the responsible doctor and
 * preparation evidence next to each patient.
 */
export function NurseHomePage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const first = nurseCohort[0];

  return (
    <div data-surface="NUR-01" data-testid="nurse-home">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("surface.NUR-01")}
      />
      <div className="pc-page__body">
        <SampleBanner label={t("app.demoTag")} title={t("nurse.banner")}>
          <p className="pc-note">
            {t("nurse.sampleNurse", {
              name: sampleStaffName(SAMPLE_NURSE_STAFF, locale),
            })}{" "}
            · {t("nurse.sampleIdentity")}
          </p>
        </SampleBanner>

        <section>
          <h2 className="pc-card__title nurse-section-title">
            {t("nurse.duties")}
          </h2>
          <p className="pc-note">{t("nurse.dutiesHint")}</p>
          <div className="pc-tilegrid">
            {nurseDutyTiles.map((tile) => {
              const href =
                tile.id === "photos" && first
                  ? ROUTES.photos(first.patientId)
                  : `${ROUTES.nurseTasks}?tab=${tile.taskTab}${
                      first
                        ? `&patient=${encodeURIComponent(first.patientId)}&appointment=${encodeURIComponent(first.appointmentId)}`
                        : ""
                    }`;
              return (
                <Link className="pc-tile" key={tile.id} to={href}>
                  <span className="pc-tile__icon">
                    <SurfaceIcon name={tile.icon} />
                  </span>
                  <span className="pc-tile__label">{tile.label[locale]}</span>
                  <span className="pc-tile__meta">{t("app.demoTag")}</span>
                </Link>
              );
            })}
          </div>
        </section>

        <Card
          title={t("nurse.cohort")}
          hint={t("nurse.cohortNote")}
          actions={
            first ? (
              <Link
                className="pc-button pc-button--primary"
                to={`${ROUTES.nurseTasks}?patient=${encodeURIComponent(first.patientId)}&appointment=${encodeURIComponent(first.appointmentId)}&tab=vitals`}
              >
                {t("nurse.openTask")}
              </Link>
            ) : undefined
          }
          flush
        >
          <DataTable
            caption={t("nurse.cohort")}
            columns={[
              { key: "patient", label: t("nurse.patient") },
              { key: "practitioner", label: t("nurse.practitioner") },
              { key: "vitals", label: t("nurse.vitals") },
              { key: "prep", label: t("nurse.prep") },
              { key: "action", label: t("nurse.action") },
            ]}
            rows={nurseCohort.map((row) => [
              <span key="patient">
                {patientLabel(row.patientId, locale)}
                <br />
                <span className="pc-note">{row.patientId}</span>
              </span>,
              practitionerLabel(row.practitionerId, locale),
              row.vitalsRecordedAt ? (
                <span key="vitals">
                  {formatDateTime(row.vitalsRecordedAt, locale)}
                </span>
              ) : (
                <span className="pc-note" key="vitals">
                  {t("nurse.noVitals")}
                </span>
              ),
              <Badge key="prep" tone={row.prepComplete ? "success" : "warning"}>
                {t(row.prepComplete ? "nurse.prepReady" : "nurse.prepWaiting")}
              </Badge>,
              <Link
                key="action"
                className="pc-button pc-button--secondary pc-button--sm"
                to={`${ROUTES.nurseTasks}?patient=${encodeURIComponent(row.patientId)}&appointment=${encodeURIComponent(row.appointmentId)}&tab=vitals`}
              >
                {t("nurse.openTask")}
              </Link>,
            ])}
            empty={t("nurse.cohortEmpty")}
          />
        </Card>

        <Card title={t("nurse.upcoming")} hint={t("nurse.assignmentNote")}>
          {upcomingProcedures.length === 0 ? (
            <p className="pc-note">{t("nurse.upcomingEmpty")}</p>
          ) : (
            upcomingProcedures.map((row) => (
              <div className="pc-listrow" key={row.id}>
                <div className="pc-listrow__grow">
                  <strong>{patientLabel(row.patientId, locale)}</strong>
                  <p className="pc-listrow__meta">
                    {t("nurse.upcomingMeta", {
                      template: templateName(row.templateId, locale),
                      time: formatTime(row.scheduledAt, locale),
                      room: roomLabel(row.room, locale),
                    })}
                  </p>
                  <p className="pc-listrow__meta">
                    {row.prepKeys
                      .map((key) => t(`nurse.prep.${key}`))
                      .join(" · ")}
                  </p>
                </div>
                <Link
                  className="pc-button pc-button--secondary pc-button--sm"
                  to={ROUTES.procedure(row.id)}
                >
                  {t("visit.openProcedure")}
                </Link>
              </div>
            ))
          )}
        </Card>
      </div>
    </div>
  );
}
