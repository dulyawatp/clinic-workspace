import { useTranslation } from "react-i18next";
import type {
  BillingPatientSummary,
  PatientSummary,
} from "../../domain/patient";
import { formatDate } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { Button } from "../../ui/Button";
import { WriteGate } from "../../ui/WriteGate";

/**
 * Persistent identity header for one patient record. It renders the record the
 * server returned and never derives an identity from a name match, so a tab
 * change or refetch cannot swap the patient underneath the reader.
 */
export function PatientHeader({
  patient,
  billingOnly = false,
}: {
  patient: PatientSummary | BillingPatientSummary;
  billingOnly?: boolean;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const unavailable = t("common.unavailable");

  return (
    <header className="patients-profile-header">
      <div className="patients-profile-identity">
        {/* The page owns the only h1; the record identity is its section title. */}
        <h2 className="patients-profile-name">{patient.fullName}</h2>
        <p className="patients-profile-id">
          <span>{t("search.patientId")}</span>{" "}
          <span className="patients-id" data-testid="patient-id">
            {patient.id}
          </span>
        </p>
        {!billingOnly && "mobileMasked" in patient && (
          <dl className="patients-profile-facts">
            <div>
              <dt>{t("profile.dob")}</dt>
              <dd>
                {patient.dob ? formatDate(patient.dob, locale) : unavailable}
              </dd>
            </div>
            <div>
              <dt>{t("search.phone")}</dt>
              <dd>{patient.mobileMasked}</dd>
            </div>
            <div>
              <dt>{t("search.lastVisit")}</dt>
              <dd>
                {patient.lastVisitAt
                  ? formatDate(patient.lastVisitAt, locale)
                  : unavailable}
              </dd>
            </div>
            <div>
              <dt>{t("profile.latestPractitioner")}</dt>
              <dd>{patient.latestPractitioner ?? unavailable}</dd>
            </div>
          </dl>
        )}
      </div>
      {!billingOnly && (
        <div className="patients-profile-actions">
          <WriteGate capability="write.appointment.book">
            <Button>{t("profile.book")}</Button>
          </WriteGate>
          <WriteGate capability="write.photo.upload">
            <Button>{t("profile.capture")}</Button>
          </WriteGate>
        </div>
      )}
    </header>
  );
}
