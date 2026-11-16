import { useTranslation } from "react-i18next";
import { Badge, Button, Card } from "../../design/components";
import { WriteGate } from "../../ui/WriteGate";
import type { Photo } from "./model";

/**
 * PAT-04 designed intake flow: consent → originals → tag & link. The capture
 * and upload actions are the designed controls only; `write.photo.upload` is
 * never enabled by the installed session, so they stay closed. Photo saving in
 * the synthetic lab workspace keeps its existing native flow, untouched.
 */
export function PhotoIntake({
  photos,
  onRecordConsent,
}: {
  photos: readonly Photo[];
  onRecordConsent: () => void;
}) {
  const { t } = useTranslation();
  const missing = photos.some((photo) => photo.consentStatus === "missing");
  return (
    <Card title={t("photos.intakeFlow")}>
      <ol className="photos-intake">
        <li className="photos-intake-step">
          <div>
            <strong>{t("photos.intakeStep.consent")}</strong>
            <p>
              {missing ? (
                <Badge tone="warning">{t("photos.intakeConsentMissing")}</Badge>
              ) : (
                <Badge tone="success">{t("photos.intakeConsentRecorded")}</Badge>
              )}
            </p>
          </div>
          <Button onClick={onRecordConsent}>{t("photos.consentCapture")}</Button>
        </li>
        <li className="photos-intake-step">
          <div>
            <strong>{t("photos.intakeStep.capture")}</strong>
            <p>
              {photos.length === 0
                ? t("photos.intakeNoOriginals")
                : t("photos.intakeOriginals", { count: photos.length })}
            </p>
          </div>
          <div className="photos-intake-actions">
            <WriteGate
              capability="write.photo.upload"
              reason={t("photos.intakeUnavailable")}
            >
              <button type="button" className="pc-button pc-button--secondary">
                {t("photos.intakeStart")}
              </button>
            </WriteGate>
            <WriteGate
              capability="write.photo.upload"
              reason={t("photos.intakeUnavailable")}
            >
              <button type="button" className="pc-button pc-button--secondary">
                {t("photos.intakeChoose")}
              </button>
            </WriteGate>
          </div>
        </li>
        <li className="photos-intake-step">
          <div>
            <strong>{t("photos.intakeStep.tag")}</strong>
            <p>{t("photos.intakeTagHint")}</p>
          </div>
        </li>
      </ol>
    </Card>
  );
}
