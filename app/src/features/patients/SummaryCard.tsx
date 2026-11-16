import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Card } from "../../design/components";
import type { BillingPatientSummary, PatientSummary } from "../../domain/patient";
import { formatDate } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";

/**
 * PAT-03 patient summary card: last visit and latest practitioner exactly as
 * the profile read declares them. Nothing is inferred from a timeline row, and
 * a patient without history reads as "no previous visit", never a blank.
 */
export function SummaryCard({
  patient,
  canOpenPhotos,
}: {
  patient: PatientSummary | BillingPatientSummary;
  canOpenPhotos: boolean;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const lastVisitAt =
    "lastVisitAt" in patient ? patient.lastVisitAt : undefined;
  const practitioner =
    "latestPractitioner" in patient ? patient.latestPractitioner : undefined;
  return (
    <Card title={t("patientSummary.title")}>
      <dl className="patients-summary-facts">
        <div>
          <dt>{t("patientSummary.lastVisit")}</dt>
          <dd>
            {lastVisitAt
              ? formatDate(lastVisitAt, locale)
              : t("patientSummary.noVisit")}
          </dd>
        </div>
        <div>
          <dt>{t("profile.latestPractitioner")}</dt>
          <dd>{practitioner ?? t("common.unavailable")}</dd>
        </div>
      </dl>
      {canOpenPhotos && (
        <p>
          <Link className="patients-open" to={ROUTES.photos(patient.id)}>
            {t("patientSummary.openPhotos")}
          </Link>
        </p>
      )}
    </Card>
  );
}
