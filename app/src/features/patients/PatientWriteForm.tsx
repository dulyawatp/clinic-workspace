import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "../../ui/Button";
import { patientWriteLocked, saveNativePatient } from "../../transport/native/patient-write";
import type { LivePatientDetail } from "./live-patient";

export function PatientWriteForm({ patient }: { patient?: LivePatientDetail }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState(patient?.first_name ?? "");
  const [last, setLast] = useState(patient?.last_name ?? "");
  const [sex, setSex] = useState(
    ["Male", "Female", "Other", "Unknown"].find((value) => value.toLowerCase() === patient?.sex?.toLowerCase()) ?? "",
  );
  const [dob, setDob] = useState(patient?.dob ?? "");
  const [locked, setLocked] = useState(patientWriteLocked);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const title = t(patient ? "patientWrite.edit" : "patientWrite.create");
  useEffect(() => {
    const syncLock = () => setLocked(patientWriteLocked());
    window.addEventListener("storage", syncLock);
    return () => window.removeEventListener("storage", syncLock);
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || locked || patientWriteLocked()) {
      setLocked(true);
      return;
    }
    const date = new Date(`${dob}T00:00:00Z`);
    if (!first.trim() || !last.trim() || !["Male", "Female", "Other", "Unknown"].includes(sex) ||
        !/^\d{4}-\d{2}-\d{2}$/.test(dob) || Number.isNaN(date.valueOf()) ||
        date.toISOString().slice(0, 10) !== dob || dob > new Date().toISOString().slice(0, 10) ||
        (patient && !patient.modified)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setSaved(false);
    setSaving(true);
    try {
      await saveNativePatient({
        first_name: first.trim(), last_name: last.trim(), sex, dob,
        operation_key: crypto.randomUUID(),
        ...(patient ? { workspace_patient_id: patient.workspace_patient_id, base_modified: patient.modified } : {}),
      });
      setSaved(true);
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["live-directory"] });
      void queryClient.invalidateQueries({ queryKey: ["live-patient"] });
    } catch {
      setLocked(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="patients-write" aria-label={title}>
      {!open && <Button variant="secondary" onClick={() => setOpen(true)}>{title}</Button>}
      {saved && <p role="status">{t("patientWrite.saved")}</p>}
      {locked && <p role="alert">{t("patientWrite.uncertain")}</p>}
      {patient && !patient.modified && <p role="status">{t("patientWrite.noVersion")}</p>}
      {open && (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <h3>{title}</h3>
          <p>{t("patientWrite.syntheticOnly")}</p>
          <div className="patients-write-fields">
            <div className="patients-field"><label htmlFor={`${id}-first`}>{t("patientWrite.first")}</label><input id={`${id}-first`} value={first} maxLength={140} required onChange={(event) => setFirst(event.target.value)} /></div>
            <div className="patients-field"><label htmlFor={`${id}-last`}>{t("patientWrite.last")}</label><input id={`${id}-last`} value={last} maxLength={140} required onChange={(event) => setLast(event.target.value)} /></div>
            <div className="patients-field"><label htmlFor={`${id}-sex`}>{t("field.sex")}</label><select id={`${id}-sex`} value={sex} required onChange={(event) => setSex(event.target.value)}><option value="">{t("patientWrite.select")}</option>{["Male", "Female", "Other", "Unknown"].map((value) => <option key={value} value={value}>{t(`patient.sex${value}`)}</option>)}</select></div>
            <div className="patients-field"><label htmlFor={`${id}-dob`}>{t("profile.dob")}</label><input id={`${id}-dob`} type="date" value={dob} required onChange={(event) => setDob(event.target.value)} /></div>
          </div>
          {invalid && <p role="alert">{t("patientWrite.invalid")}</p>}
          <div className="patients-write-actions">
            <Button type="submit" disabled={locked || saving || Boolean(patient && !patient.modified)}>{saving ? t("patientWrite.saving") : t("patientWrite.save")}</Button>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>{t("patientWrite.close")}</Button>
          </div>
        </form>
      )}
    </section>
  );
}
