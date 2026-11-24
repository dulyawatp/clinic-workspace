import { type FormEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import { captureClinicRequestContext } from "../../transport/native/frappe";
import {
  lookupNativeOwnerDoctorReuseChoices,
  lookupNativeOwnerDoctorReuseReview,
  type OwnerDoctorFinishLookup,
  type OwnerDoctorReuseChoices,
} from "../../transport/native/owner-doctor-finish";

export function DoctorScheduleReuse({ practitioner, disabled, choosing, onChoosingChange, onReviewed }: {
  practitioner: string;
  disabled: boolean;
  choosing: boolean;
  onChoosingChange: (choosing: boolean) => void;
  onReviewed: (review: Extract<OwnerDoctorFinishLookup, { kind: "ready" }>) => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const [choices, setChoices] = useState<{ data: OwnerDoctorReuseChoices; isCurrent: () => boolean } | null>(null);
  const [schedule, setSchedule] = useState("");
  const [serviceUnit, setServiceUnit] = useState("");
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState<"unavailable" | "error" | "expired" | null>(null);
  const request = useRef<AbortController | null>(null);
  const scheduleInput = useRef<HTMLSelectElement>(null);
  const region = useRef<HTMLElement>(null);
  const opened = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const isCurrent = captureClinicRequestContext();
    void lookupNativeOwnerDoctorReuseChoices(practitioner, controller.signal).then((data) => {
      if (data && !controller.signal.aborted && isCurrent()) setChoices({ data, isCurrent });
    });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [practitioner]);

  useLayoutEffect(() => {
    if (choosing) scheduleInput.current?.focus();
    else if (opened.current) region.current?.querySelector("button")?.focus();
  }, [choosing]);

  if (!choices?.isCurrent() || !choices.data.schedules.length || !choices.data.serviceUnits.length) return null;
  const { data, isCurrent } = choices;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || disabled || !isCurrent() ||
        !data.schedules.some(({ name }) => name === schedule) ||
        !data.serviceUnits.some(({ name }) => name === serviceUnit)) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setIssue(null);
    const result = await lookupNativeOwnerDoctorReuseReview(practitioner, { schedule, serviceUnit }, controller.signal);
    if (controller.signal.aborted || !isCurrent()) return;
    request.current = null;
    setBusy(false);
    if (result.kind === "ready") onReviewed(result);
    else setIssue(result.kind);
  }

  function cancel() {
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setIssue(null);
    setSchedule("");
    setServiceUnit("");
    onChoosingChange(false);
  }

  return (
    <section className="owner-doctor-reuse" ref={region} aria-labelledby={titleId}>
      <h4 id={titleId}>{t("ownerDoctor.reuse.title")}</h4>
      {!choosing ? (
        <>
          <p className="pc-note">{t("ownerDoctor.reuse.hint")}</p>
          <Button disabled={disabled} onClick={() => {
            if (!isCurrent()) return;
            opened.current = true;
            onChoosingChange(true);
          }} data-testid="owner-doctor-reuse-start">{t("ownerDoctor.reuse.open")}</Button>
        </>
      ) : (
        <form className="owner-doctor-form" onSubmit={(event) => void submit(event)}>
          <label className="pc-field">
            <span className="pc-field__label">{t("ownerDoctor.reuse.schedule")}</span>
            <select ref={scheduleInput} required disabled={busy} value={schedule} onChange={(event) => {
              setSchedule(event.target.value);
              setIssue(null);
            }} data-testid="owner-doctor-reuse-schedule">
              <option value="">{t("ownerDoctor.reuse.chooseSchedule")}</option>
              {data.schedules.map(({ name }) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="pc-field">
            <span className="pc-field__label">{t("ownerHours.unit")}</span>
            <select required disabled={busy} value={serviceUnit} onChange={(event) => {
              setServiceUnit(event.target.value);
              setIssue(null);
            }} data-testid="owner-doctor-reuse-unit">
              <option value="">{t("ownerHours.chooseUnit")}</option>
              {data.serviceUnits.map(({ name }) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          {busy && <p role="status">{t("ownerDoctor.reuse.loading")}</p>}
          {issue && <p role="alert" data-testid="owner-doctor-reuse-read-error">{t(issue === "expired" ? "ownerDoctor.finish.read.expired" : "ownerDoctor.reuse.readError")}</p>}
          <div className="owner-doctor-actions">
            <Button variant="primary" type="submit" disabled={busy || !schedule || !serviceUnit} data-testid="owner-doctor-reuse-review">
              {t("ownerDoctor.reuse.review")}
            </Button>
            <Button onClick={cancel} data-testid="owner-doctor-reuse-cancel">{t("ownerDoctor.reuse.back")}</Button>
          </div>
        </form>
      )}
    </section>
  );
}
