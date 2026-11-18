import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "../../ui/Button";
import { nativeTransportEnabled } from "../../transport/native/flag";
import {
  PatientWriteRejectedError,
  saveNativePatient,
  type PatientWriteFailureKind,
} from "../../transport/native/patient-write";
import type { LivePatientDetail } from "./live-patient";
import {
  patientGenderLabelKey,
  usePatientGenderOptions,
} from "./use-patient-genders";

export function PatientWriteForm({ patient }: { patient?: LivePatientDetail }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const id = useId();
  const native = nativeTransportEnabled();
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState(patient?.first_name ?? "");
  const [last, setLast] = useState(patient?.last_name ?? "");
  const [sex, setSex] = useState(patient?.sex ?? "");
  const genders = usePatientGenderOptions(open);
  const genderItems = genders.state === "ready" ? genders.items : null;
  const [dob, setDob] = useState(patient?.dob ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<
    PatientWriteFailureKind | "unknown" | null
  >(null);
  const [invalid, setInvalid] = useState(false);
  const title = t(patient ? "patientWrite.edit" : "patientWrite.create");

  // A stored sex the site no longer offers is never rewritten silently: the
  // selection is cleared so the reader must choose from the current list.
  useEffect(() => {
    if (!native || !open || genders.state !== "ready") return;
    if (!genders.items.includes(sex)) setSex("");
  }, [native, open, genders, sex]);

  /** Best-effort record refresh; it never changes a decided write outcome. */
  async function refreshRecords() {
    try {
      await queryClient.invalidateQueries({ queryKey: ["live-directory"] });
      await queryClient.invalidateQueries({ queryKey: ["live-patient"] });
    } catch {
      // A failed refresh leaves the reconciliation to the reader's next view.
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const date = new Date(`${dob}T00:00:00Z`);
    if (!first.trim() || !last.trim() || genderItems === null ||
        !genderItems.includes(sex) ||
        !/^\d{4}-\d{2}-\d{2}$/.test(dob) || Number.isNaN(date.valueOf()) ||
        date.toISOString().slice(0, 10) !== dob || dob > new Date().toISOString().slice(0, 10) ||
        (patient && !patient.modified)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setSaved(false);
    setFailure(null);
    setSaving(true);
    let confirmed = false;
    try {
      await saveNativePatient({
        first_name: first.trim(), last_name: last.trim(), sex, dob,
        operation_key: crypto.randomUUID(),
        ...(patient ? { workspace_patient_id: patient.workspace_patient_id, base_modified: patient.modified } : {}),
      });
      confirmed = true;
    } catch (error) {
      // A deterministic refusal is a known failure; anything else (lost,
      // unreadable) is an unknown outcome. Nothing is retried automatically.
      setFailure(
        error instanceof PatientWriteRejectedError ? error.kind : "unknown",
      );
    }
    if (confirmed) {
      setSaved(true);
      setOpen(false);
    }
    // Refresh either way: a confirmed write shows the new record, and an
    // unknown outcome is reconciled before the reader can repeat it.
    await refreshRecords();
    setSaving(false);
  }

  return (
    <section className="patients-write" aria-label={title}>
      {!open && <Button variant="secondary" onClick={() => setOpen(true)}>{title}</Button>}
      {saved && <p role="status">{t("patientWrite.saved")}</p>}
      {failure !== null && (
        <p role="alert">
          {t(
            failure === "unknown"
              ? "patientWrite.uncertain"
              : `patientWrite.${failure}`,
          )}
        </p>
      )}
      {patient && !patient.modified && <p role="status">{t("patientWrite.noVersion")}</p>}
      {open && (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <h3>{title}</h3>
          <p>{t("patientWrite.syntheticOnly")}</p>
          <div className="patients-write-fields">
            <div className="patients-field"><label htmlFor={`${id}-first`}>{t("patientWrite.first")}</label><input id={`${id}-first`} value={first} maxLength={140} required onChange={(event) => setFirst(event.target.value)} /></div>
            <div className="patients-field"><label htmlFor={`${id}-last`}>{t("patientWrite.last")}</label><input id={`${id}-last`} value={last} maxLength={140} required onChange={(event) => setLast(event.target.value)} /></div>
            <div className="patients-field"><label htmlFor={`${id}-sex`}>{t("field.sex")}</label><select id={`${id}-sex`} value={sex} required disabled={genderItems === null} onChange={(event) => setSex(event.target.value)}><option value="">{t("patientWrite.select")}</option>{genderItems?.map((value) => { const key = patientGenderLabelKey(value); return <option key={value} value={value}>{key ? t(key) : value}</option>; })}</select></div>
            <div className="patients-field"><label htmlFor={`${id}-dob`}>{t("profile.dob")}</label><input id={`${id}-dob`} type="date" value={dob} required onChange={(event) => setDob(event.target.value)} /></div>
          </div>
          {genders.state === "loading" && <p role="status">{t("patientGenders.loading")}</p>}
          {genders.state === "error" && <p role="alert">{t("patientGenders.error")}</p>}
          {invalid && <p role="alert">{t("patientWrite.invalid")}</p>}
          <div className="patients-write-actions">
            <Button type="submit" disabled={saving || genderItems === null || Boolean(patient && !patient.modified)}>{saving ? t("patientWrite.saving") : t("patientWrite.save")}</Button>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>{t("patientWrite.close")}</Button>
          </div>
        </form>
      )}
    </section>
  );
}
