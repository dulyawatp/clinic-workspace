import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Badge } from "../../design/components";
import { Card } from "../../design/components";
import { SampleBanner } from "../../design/components";
import { consentForm, consentRecords } from "../../fixtures/data-clinical";
import { businessDay } from "../../fixtures/data-ops";
import { formatDate } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { bangkokTodayKey } from "./patient-create";

/**
 * PAT-03 consent-evidence block. Every populated row is authored Demo/Sample
 * data (`data-clinical.ts`), so the block always carries the sample label; a
 * patient without an authored record reads the designed empty state instead of
 * a borrowed consent.
 */
export function ConsentEvidence({
  patientId,
  canOpenPhotos,
}: {
  patientId: string;
  canOpenPhotos: boolean;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const record = consentRecords.find((row) => row.patientId === patientId);
  const today = bangkokTodayKey();
  const validUntil = record === undefined ? null : businessDay(record.validUntil);
  const tone =
    record === undefined
      ? "neutral"
      : record.status === "Revoked"
        ? "danger"
        : validUntil !== null && validUntil < today
          ? "warning"
          : "success";

  return (
    <Card title={t("consent.title")}>
      {record === undefined ? (
        <>
          <p className="patients-consent-empty">{t("consent.empty")}</p>
          <p className="patients-consent-empty">{t("consent.emptyBody")}</p>
        </>
      ) : (
        <>
          <SampleBanner
            label={t("app.demoTag")}
            title={t("patientSummary.sampleNote")}
          />
          <p>
            <Badge tone={tone}>{record.stateLabel[locale]}</Badge>
          </p>
          <dl className="patients-consent-facts">
            <dt>{t("patientSummary.scope")}</dt>
            <dd>{t("patientSummary.scopeValue")}</dd>
            <dt>{t("patientSummary.version")}</dt>
            <dd>
              {record.version} · {record.evidenceRef}
            </dd>
            <dt>{t("patientSummary.signer")}</dt>
            <dd>
              {record.signerName} · {formatDate(record.consentDate, locale)}
            </dd>
            <dt>{t("consent.form")}</dt>
            <dd>
              {consentForm.id} · {consentForm.version}
            </dd>
            <dt>{t("consent.capturedBy")}</dt>
            <dd>{record.capturedBy}</dd>
            <dt>{t("consent.signature")}</dt>
            <dd>{record.signatureFile}</dd>
            {record.signerIsGuardian && (
              <>
                <dt>{t("consent.guardian")}</dt>
                <dd>{t("consent.guardianLine")}</dd>
              </>
            )}
            {record.validUntil !== null && (
              <>
                <dt>{t("consent.validUntil")}</dt>
                <dd>{formatDate(record.validUntil, locale)}</dd>
              </>
            )}
            {record.withdrawnAt !== null && (
              <>
                <dt>{t("consent.withdrawnAt")}</dt>
                <dd>{formatDate(record.withdrawnAt, locale)}</dd>
              </>
            )}
            {record.withdrawnReason !== null && (
              <>
                <dt>{t("consent.withdrawnReason")}</dt>
                <dd>{record.withdrawnReason}</dd>
              </>
            )}
          </dl>
        </>
      )}
      {canOpenPhotos && (
        <p>
          <Link className="patients-open" to={ROUTES.photos(patientId)}>
            {t("patientSummary.openPhotos")}
          </Link>
        </p>
      )}
    </Card>
  );
}
