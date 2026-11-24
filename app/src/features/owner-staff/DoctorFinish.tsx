import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button } from "../../design/components";
import { captureClinicRequestContext } from "../../transport/native/frappe";
import {
  finishNativeOwnerDoctor,
  lookupNativeOwnerDoctorFinish,
  lookupNativeOwnerDoctorReuseReview,
  type OwnerDoctorFinishLookup,
  type OwnerDoctorFinishResult,
  type OwnerDoctorReuseSelection,
} from "../../transport/native/owner-doctor-finish";
import { DoctorInvitation } from "./DoctorInvitation";
import { ReviewedWorkingHours } from "./DraftWorkingHours";

export type BlockedFinishReview = { kind: "stale" | "uncertain"; reuse?: OwnerDoctorReuseSelection };
export type FinishReview = OwnerDoctorFinishLookup | BlockedFinishReview;
type FinishReceipt = Extract<OwnerDoctorFinishResult, { kind: "finished" }>["receipt"];
type FinishIssue = Exclude<OwnerDoctorFinishResult["kind"], "finished">;

export function DoctorFinish({ practitioner, displayName, initialReview, onPendingChange, onBlocked, onFinished, onChangeReuse }: {
  practitioner: string;
  displayName: string;
  initialReview: FinishReview;
  onPendingChange: (pending: boolean) => void;
  onBlocked: (block: BlockedFinishReview | null) => void;
  onFinished: () => void;
  onChangeReuse: () => void;
}) {
  const { t } = useTranslation();
  const [review, setReview] = useState(initialReview);
  const [issue, setIssue] = useState<FinishIssue | null>(
    initialReview.kind === "stale" || initialReview.kind === "uncertain" ? initialReview.kind : null,
  );
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState<"reading" | "finishing" | null>(null);
  const [receipt, setReceipt] = useState<FinishReceipt | null>(null);
  const request = useRef<AbortController | null>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const interacted = useRef(false);
  const reuse = initialReview.kind === "ready" && initialReview.options.mode === "reuse"
    ? { schedule: initialReview.options.schedule, serviceUnit: initialReview.options.serviceUnit }
    : "reuse" in initialReview ? initialReview.reuse : undefined;
  const uncertainReuse = !!reuse && issue === "uncertain";

  useEffect(() => () => request.current?.abort(), []);
  useLayoutEffect(() => {
    if (interacted.current && !busy && !receipt) feedback.current?.focus();
  }, [busy, receipt]);

  async function reread() {
    // A schedule review cannot confirm whether the account or its permissions were saved.
    if (request.current || uncertainReuse) return;
    const controller = new AbortController();
    const isCurrent = captureClinicRequestContext();
    request.current = controller;
    interacted.current = true;
    setBusy("reading");
    const result = await (reuse
      ? lookupNativeOwnerDoctorReuseReview(practitioner, reuse, controller.signal)
      : lookupNativeOwnerDoctorFinish(practitioner, controller.signal));
    if (controller.signal.aborted) return;
    request.current = null;
    setBusy(null);
    if (!isCurrent()) return;
    setReview(result);
    if (result.kind === "ready") {
      setIssue(null);
      onBlocked(null);
    }
  }

  async function finish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || review.kind !== "ready" || issue || !email.trim()) return;
    const controller = new AbortController();
    const isCurrent = captureClinicRequestContext();
    request.current = controller;
    interacted.current = true;
    setBusy("finishing");
    onPendingChange(true);
    const { practitioner, practitionerVersion, schedule, scheduleVersion, slotDigest, mode, serviceUnit } = review.options;
    const input = {
      practitioner, practitionerVersion, schedule, scheduleVersion, slotDigest, email: email.trim(),
    };
    const result = await finishNativeOwnerDoctor(mode === "reuse" ? { ...input, mode, serviceUnit } : input);
    if (controller.signal.aborted) return;
    request.current = null;
    setBusy(null);
    onPendingChange(false);
    if (isCurrent() && result.kind === "finished") {
      setReceipt(result.receipt);
      onFinished();
      return;
    }
    const failure = isCurrent() && result.kind !== "finished" ? result.kind : "uncertain";
    setIssue(failure);
    if (failure === "stale" || failure === "uncertain") onBlocked({ kind: failure, reuse });
  }

  if (receipt) return (
    <div className="owner-doctor-step">
      <section className="owner-hours-receipt" data-testid="owner-doctor-finish-receipt">
        <div><strong>{t("ownerDoctor.finish.linked")}</strong> <Badge tone="success">{t("ownerStaff.enabled")}</Badge></div>
        <dl className="owner-doctor-details">
          <div><dt>{t("ownerDoctor.profile")}</dt><dd><bdi>{displayName}</bdi></dd></div>
          <div><dt>{t("ownerDoctor.finish.account")}</dt><dd><bdi>{receipt.userId}</bdi></dd></div>
          <div><dt>{t("ownerHours.unit")}</dt><dd><bdi>{receipt.serviceUnit}</bdi></dd></div>
          <div><dt>{t("ownerDoctor.finish.activeSchedule")}</dt><dd><bdi>{receipt.schedule}</bdi></dd></div>
        </dl>
      </section>
      <DoctorInvitation practitioner={receipt.practitioner} userId={receipt.userId} onPendingChange={onPendingChange} />
    </div>
  );

  const locked = issue !== null && issue !== "validation" && issue !== "email_in_use";
  const showReview = review.kind === "ready" && !locked;
  const canReread = !uncertainReuse &&
    (issue === "stale" || issue === "uncertain" || review.kind === "error" || review.kind === "unavailable");

  return (
    <div className="owner-doctor-step" data-testid="owner-doctor-finish-review">
      <div ref={feedback} tabIndex={-1} className="owner-doctor-step">
        {busy && <p role="status">{t(busy === "reading" ? (reuse ? "ownerDoctor.reuse.loading" : "ownerDoctor.finish.loading") : "ownerDoctor.finish.finishing")}</p>}
        {!busy && issue && (
          <p role="alert" className="owner-staff-edit-alert" data-testid={`owner-doctor-finish-${issue}`}>
            {t(uncertainReuse ? "ownerDoctor.reuse.uncertain" : `ownerDoctor.finish.${issue}`)}
          </p>
        )}
        {!busy && (review.kind === "error" || review.kind === "unavailable" || review.kind === "expired") && (
          <p role="status" data-testid={`owner-doctor-finish-read-${review.kind}`}>
            {t(reuse && review.kind !== "expired" ? "ownerDoctor.reuse.readError" : `ownerDoctor.finish.read.${review.kind}`)}
          </p>
        )}
        {showReview && review.kind === "ready" && (
          <dl className="owner-doctor-summary owner-doctor-details">
            <div><dt>{t("ownerHours.unit")}</dt><dd><bdi>{review.options.serviceUnit}</bdi></dd></div>
            <div><dt>{t(reuse ? "ownerDoctor.reuse.schedule" : "ownerDoctor.finish.schedule")}</dt><dd><bdi>{review.options.schedule}</bdi></dd></div>
          </dl>
        )}
      </div>
      {showReview && review.kind === "ready" && (
        <>
          {review.options.mode === "reuse" ? (
            <>
              <p className="owner-doctor-reuse-warning" data-testid="owner-doctor-reuse-warning">{t("ownerDoctor.reuse.warning")}</p>
              <div className="owner-doctor-reuse" data-testid="owner-doctor-reuse-hours">
                <h4>{t("ownerDoctor.reuse.hours")}</h4>
                <ul>
                  {review.options.slots.map((slot, index) => (
                    <li key={index}>{t(`ownerHours.day.${slot.day}`)}: <bdi>{slot.from_time}–{slot.to_time}</bdi></li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <div data-testid="owner-doctor-reviewed-hours"><ReviewedWorkingHours slots={review.options.slots} /></div>
          )}
          <form className="owner-doctor-form" onSubmit={(event) => void finish(event)} data-testid="owner-doctor-finish-form">
            <p className="pc-note" id="owner-doctor-finish-email-hint">
              {t(reuse ? "ownerDoctor.reuse.emailHint" : "ownerDoctor.finish.emailHint")}
            </p>
            <label className="pc-field">
              <span className="pc-field__label">{t("ownerDoctor.finish.email")}</span>
              <input
                type="email" required maxLength={140} autoComplete="off" value={email}
                disabled={busy !== null} aria-describedby="owner-doctor-finish-email-hint"
                aria-invalid={issue === "email_in_use" || issue === "validation" ? true : undefined}
                onChange={(event) => {
                  setEmail(event.target.value);
                  if (!locked) setIssue(null);
                }}
                data-testid="owner-doctor-finish-email"
              />
            </label>
            <Button variant="primary" type="submit" disabled={busy !== null || issue !== null} data-testid="owner-doctor-finish">
              {t("ownerDoctor.finish.action")}
            </Button>
          </form>
        </>
      )}
      {canReread && (
        <Button disabled={busy !== null} onClick={() => void reread()} data-testid="owner-doctor-finish-check">
          {t(reuse ? "ownerDoctor.reuse.check" : "ownerDoctor.finish.check")}
        </Button>
      )}
      {reuse && (showReview || issue === "stale") && (
        <Button disabled={busy !== null} onClick={() => {
          if (request.current) return;
          onBlocked(null);
          onChangeReuse();
        }} data-testid="owner-doctor-reuse-change">{t("ownerDoctor.reuse.change")}</Button>
      )}
    </div>
  );
}
