import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import type { PatientSummary } from "../../domain/patient";
import { StatusChip } from "../../ui/StatusChip";

/**
 * One directory row. Opening a record is read-only navigation, so it is an
 * ordinary router link to the canonical patient builder — never a write gate,
 * and never a hand-assembled path.
 */
export function PatientRow({ patient }: { patient: PatientSummary }) {
  const { t } = useTranslation();

  return (
    <tr className="patients-row">
      <td
        headers="directory-name"
        data-label={t("search.patientName")}
        className="patients-name"
      >
        <div>
          <span className="patients-name-text">{patient.fullName}</span>
          {patient.duplicateCandidate === true && (
            <StatusChip tone="warning" label={t("patients.duplicate")} />
          )}
          <Link
            className="patients-open"
            to={ROUTES.patient(patient.id)}
            aria-label={`${t("action.openRecord")} · ${patient.fullName}`}
          >
            {t("action.openRecord")}
          </Link>
        </div>
      </td>
      <td
        headers="directory-id"
        data-label={t("search.patientId")}
        className="patients-id"
      >
        {patient.id}
      </td>
      <td
        headers="directory-phone"
        data-label={t("search.phone")}
        className="patients-mobile"
      >
        {patient.mobileMasked}
      </td>
      <td
        headers="directory-last-visit"
        data-label={t("search.lastVisit")}
        className="patients-last-visit"
      >
        {patient.lastVisitAt !== undefined ? (
          <time dateTime={patient.lastVisitAt}>{patient.lastVisitAt}</time>
        ) : (
          t("common.unavailable")
        )}
      </td>
    </tr>
  );
}
