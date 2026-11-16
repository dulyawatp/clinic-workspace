import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Badge } from "../../design/components";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { proceduresForPatient, templateName } from "./sample-scope";

/** Designed photos section: entry points only, originals stay untouched. */
export function PhotosSection({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  return (
    <div className="pc-stack">
      <p className="clinical-notice">{t("visit.photosNotice")}</p>
      <div className="pc-row">
        <Link
          className="pc-button pc-button--secondary"
          to={ROUTES.photos(patientId)}
        >
          {t("visit.patientPhotos")}
        </Link>
        <Link
          className="pc-button pc-button--secondary"
          to={ROUTES.photoCompare(patientId)}
        >
          {t("visit.compareAndAnnotate")}
        </Link>
      </div>
    </div>
  );
}

/**
 * Designed procedures section. A procedure without a resolved template lot is
 * not listed (its consumption cannot be evidenced), and every row opens the
 * DOC-03 detail instead of confirming anything from here.
 */
export function ProceduresSection({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const rows = proceduresForPatient(patientId);
  return (
    <div className="pc-stack">
      {rows.length === 0 ? (
        <p className="pc-note">{t("visit.noProcedures")}</p>
      ) : (
        <ul className="pc-lab-list">
          {rows.map((row) => (
            <li className="pc-listrow" key={row.id}>
              <div className="pc-listrow__grow">
                <strong>{templateName(row.templateId, locale)}</strong>
                <p className="pc-listrow__meta">
                  {row.id} · {formatDateTime(row.at, locale)}
                </p>
              </div>
              <Badge tone={row.status === "scheduled" ? "info" : "warning"}>
                {t(
                  row.status === "scheduled"
                    ? "procedure.scheduled"
                    : row.status === "completed"
                      ? "status.completed"
                      : "status.inProgress",
                )}
              </Badge>
              <Link
                className="pc-button pc-button--secondary pc-button--sm"
                to={ROUTES.procedure(row.id)}
              >
                {t("visit.openProcedure")}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="pc-note">{t("visit.proceduresNotice")}</p>
    </div>
  );
}
