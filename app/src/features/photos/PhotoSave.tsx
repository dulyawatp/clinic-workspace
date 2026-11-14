import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type PhotoOutcome,
  photoStatus,
  savePhoto,
} from "../../transport/photo-save";
import { useCapabilities } from "../shell/CapabilityProvider";

function encode(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("File read failed"));
    reader.onload = () =>
      typeof reader.result === "string"
        ? resolve(reader.result.split(",")[1] ?? "")
        : reject(new Error("File read failed"));
    reader.readAsDataURL(file);
  });
}

/** syntheticPatient is a workspace key verified against the authenticated live mapping. */
export function PhotoSave({
  syntheticPatient,
  onSaved,
}: {
  syntheticPatient?: string;
  onSaved?: () => void;
}) {
  const { t } = useTranslation();
  const enabled =
    useCapabilities().get("write.photo.upload") === "enabled" &&
    !!syntheticPatient?.trim();
  const [file, setFile] = useState<File>();
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<
    PhotoOutcome | { status: "unknown" }
  >();
  const locked = busy || !!outcome;

  async function submit() {
    if (!enabled || !file || locked || !syntheticPatient) return;
    setBusy(true);
    try {
      const base64 = await encode(file);
      const result = await savePhoto(
        syntheticPatient,
        crypto.randomUUID(),
        base64,
      );
      setOutcome(result);
      if (result.status === "saved") onSaved?.();
    } catch {
      setOutcome({ status: "unknown" });
    } finally {
      setBusy(false);
    }
  }
  async function check() {
    if (!outcome || !("operation_id" in outcome) || busy) return;
    const id = outcome.operation_id;
    setBusy(true);
    try {
      const result = await photoStatus(id);
      setOutcome(
        result.operation_id === id
          ? result
          : { status: "unknown", operation_id: id },
      );
      if (result.operation_id === id && result.status === "saved") onSaved?.();
    } catch {
      setOutcome({ status: "unknown", operation_id: id });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="photos-card photos-save"
      aria-labelledby="photo-save-title"
    >
      <p className="photos-eyebrow">{t("photoSave.eyebrow")}</p>
      <h3 id="photo-save-title">{t("photoSave.title")}</h3>
      <p>{t("photoSave.description")}</p>
      {!enabled && <p>{t("photoSave.unavailable")}</p>}
      <label htmlFor="photo-save-file">{t("photoSave.choose")}</label>
      <input
        id="photo-save-file"
        type="file"
        accept="image/jpeg,image/png"
        disabled={!enabled || locked}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          const bad =
            !!selected &&
            (!["image/jpeg", "image/png"].includes(selected.type) ||
              selected.size === 0 ||
              selected.size > 8 * 1024 * 1024);
          setInvalid(bad);
          setFile(bad ? undefined : selected);
        }}
      />
      {invalid && <p role="alert">{t("photoSave.invalid")}</p>}
      <button
        className="ui-button ui-button--primary"
        type="button"
        disabled={!enabled || !file || locked}
        onClick={submit}
      >
        {t("photoSave.save")}
      </button>
      {busy && <p role="status">{t("photoSave.working")}</p>}
      {outcome && (
        <div className="photos-save-status" role="status">
          <strong>{t(`photoSave.${outcome.status}`)}</strong>
          {"operation_id" in outcome && (
            <p>
              {t("photoSave.operation")}: {outcome.operation_id}
            </p>
          )}
        </div>
      )}
      {outcome &&
        "operation_id" in outcome &&
        (outcome.status === "pending" || outcome.status === "unknown") && (
          <button
            className="ui-button ui-button--secondary"
            type="button"
            disabled={busy}
            onClick={check}
          >
            {t("photoSave.check")}
          </button>
        )}
    </section>
  );
}
