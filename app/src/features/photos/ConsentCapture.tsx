import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button } from "../../design/components";
import { consentForm, consentRecords } from "../../fixtures/data-clinical";
import { useLocale } from "../../i18n/LocaleProvider";
import { WriteGate } from "../../ui/WriteGate";

/**
 * PAT-04 consent capture/record UI.
 *
 * `write.photo.consent` is the only capability this control names, and the
 * installed session never enables it: the form is the designed control, the
 * save action stays visibly closed, and no consent row is written anywhere.
 * An authored fixture record is shown as recorded Demo/Sample evidence.
 */
export function ConsentCapture({
  patientId,
  open,
  onClose,
}: {
  patientId: string;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const ids = useId();
  const [signer, setSigner] = useState("");
  const [signedOn, setSignedOn] = useState("");
  const [evidence, setEvidence] = useState("");
  const [ack, setAck] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const authored = consentRecords.find((row) => row.patientId === patientId);

  return (
    <>
      {authored !== undefined && (
        <p className="photos-consent-state">
          <Badge
            tone={authored.status === "Revoked" ? "danger" : "success"}
          >
            {authored.stateLabel[locale]}
          </Badge>{" "}
          {t("photos.consentRecorded")}
        </p>
      )}
      {open && (
        <section
          className="photos-consent-record"
          role="region"
          aria-labelledby={`${ids}-title`}
        >
          <h3 id={`${ids}-title`}>{t("photos.consentDialogTitle")}</h3>
          <p>{t("photos.consentNotWired")}</p>
          <p>
            {t("photos.consentVersion", { version: consentForm.version })} ·{" "}
            {t("photos.consentScopeLine")}
          </p>
          <div className="photos-consent-form">
            <div className="photos-field">
              <label htmlFor={`${ids}-signer`}>
                {t("photos.consentSigner")}
              </label>
              <input
                id={`${ids}-signer`}
                value={signer}
                onChange={(event) => setSigner(event.target.value)}
              />
            </div>
            <div className="photos-field">
              <label htmlFor={`${ids}-date`}>
                {t("photos.consentSignedDate")}
              </label>
              <input
                id={`${ids}-date`}
                type="date"
                value={signedOn}
                onChange={(event) => setSignedOn(event.target.value)}
              />
            </div>
            <div className="photos-field">
              <label htmlFor={`${ids}-evidence`}>
                {t("photos.consentEvidenceRef")}
              </label>
              <input
                id={`${ids}-evidence`}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
              />
            </div>
          </div>
          <label className="photos-check">
            <input
              type="checkbox"
              checked={ack}
              onChange={(event) => setAck(event.target.checked)}
            />
            <span>{t("photos.consentAck")}</span>
          </label>
          {invalid && (
            <p role="alert" className="photos-consent-state">
              {t("photos.consentValidation")}
            </p>
          )}
          <div className="photos-consent-record-actions">
            <WriteGate
              capability="write.photo.consent"
              reason={t("photos.consentNotWired")}
            >
              <button
                type="button"
                className="pc-button pc-button--primary"
                onClick={() => {
                  if (
                    signer.trim() === "" ||
                    signedOn.trim() === "" ||
                    evidence.trim() === "" ||
                    !ack
                  ) {
                    setInvalid(true);
                    return;
                  }
                  setInvalid(false);
                }}
              >
                {t("photos.consentCapture")}
              </button>
            </WriteGate>
            <Button onClick={onClose}>{t("action.close")}</Button>
          </div>
        </section>
      )}
    </>
  );
}
