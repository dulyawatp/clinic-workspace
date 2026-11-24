import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import { captureClinicRequestContext } from "../../transport/native/frappe";
import {
  lookupNativeOwnerDoctorInvitation,
  sendNativeOwnerDoctorInvitation,
  type OwnerDoctorInvitationResult,
} from "../../transport/native/owner-doctor-invitation";

export function DoctorInvitation({ practitioner, userId, onPendingChange }: {
  practitioner?: string;
  userId: string;
  onPendingChange: (pending: boolean) => void;
}) {
  const { t } = useTranslation();
  const headingId = useId();
  const warningId = useId();
  const [status, setStatus] = useState<OwnerDoctorInvitationResult | null>(null);
  const [discoveredPractitioner, setDiscoveredPractitioner] = useState<string>();
  const [busy, setBusy] = useState<"reading" | "sending" | null>(null);
  const request = useRef<AbortController | null>(null);
  const feedback = useRef<HTMLParagraphElement>(null);
  const interacted = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const isCurrent = captureClinicRequestContext();
    void lookupNativeOwnerDoctorInvitation({ practitioner, userId }, controller.signal).then((result) => {
      if (controller.signal.aborted || !isCurrent()) return;
      if (practitioner !== undefined) {
        if (result.kind === "ready") setStatus(result);
      } else if (result.kind === "ready" && result.invitation.practitioner && result.invitation.status !== "unavailable") {
        setDiscoveredPractitioner(result.invitation.practitioner);
        setStatus(result);
      } else {
        setStatus(result.kind === "denied" || result.kind === "expired" ? result : {
          kind: "ready", invitation: { status: "unavailable" },
        });
      }
    });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [practitioner, userId]);

  useLayoutEffect(() => {
    if (interacted.current && !busy) feedback.current?.focus();
  }, [busy]);

  const invitation = status?.kind === "ready" ? status.invitation : null;
  const target = practitioner ?? discoveredPractitioner;
  const retryQueue = invitation?.status === "failed" ? invitation.queueId : undefined;
  const canSend = Boolean(target && (invitation?.status === "not_queued" || retryQueue));

  async function act(action: "check" | "send") {
    if (!target || request.current || (action === "send" && !canSend)) return;
    const controller = new AbortController();
    const isCurrent = captureClinicRequestContext();
    request.current = controller;
    interacted.current = true;
    setBusy(action === "send" ? "sending" : "reading");
    if (action === "send") onPendingChange(true);
    let result = action === "send"
      ? await sendNativeOwnerDoctorInvitation({
        practitioner: target, userId, ...(retryQueue ? { previousQueueId: retryQueue } : {}),
      })
      : await lookupNativeOwnerDoctorInvitation({ practitioner, userId }, controller.signal);
    if (controller.signal.aborted) return;
    if (action === "check" && practitioner === undefined && result.kind === "ready" && result.invitation.practitioner !== target) {
      result = { kind: "ready", invitation: { status: "unavailable" } };
    }
    request.current = null;
    setBusy(null);
    if (action === "send") onPendingChange(false);
    setStatus(isCurrent() ? result : { kind: "uncertain" });
  }

  if (practitioner !== undefined && (!status || (!interacted.current && invitation?.status === "unavailable"))) return null;
  const label = invitation?.status ?? (status?.kind === "uncertain" ? "unknown" : status?.kind ?? "reading");
  const canCheck = target && label !== "denied" && label !== "expired" && label !== "unavailable";

  return (
    <section className="owner-doctor-invitation" aria-labelledby={headingId} data-testid="owner-doctor-invitation">
      <h4 id={headingId}>{t("ownerDoctor.invitation.title")}</h4>
      <p className="pc-note">{t("ownerDoctor.invitation.hint")}</p>
      <p ref={feedback} tabIndex={-1} role="status" data-testid="owner-doctor-invitation-status" data-status={label}>
        {t(`ownerDoctor.invitation.${busy ?? label}`)}
      </p>
      {!busy && retryQueue && <p id={warningId} className="owner-staff-edit-alert">{t("ownerDoctor.invitation.duplicateWarning")}</p>}
      {(canSend || canCheck) && <div className="owner-doctor-actions">
        {canSend && (
          <Button
            variant="primary" disabled={busy !== null} aria-describedby={retryQueue && !busy ? warningId : undefined}
            onClick={() => void act("send")}
            data-testid={retryQueue ? "owner-doctor-invitation-retry" : "owner-doctor-invitation-send"}
          >
            {t(retryQueue ? "ownerDoctor.invitation.retry" : "ownerDoctor.invitation.send")}
          </Button>
        )}
        {canCheck && (
          <Button disabled={busy !== null} onClick={() => void act("check")} data-testid="owner-doctor-invitation-check">
            {t("ownerDoctor.invitation.check")}
          </Button>
        )}
      </div>}
    </section>
  );
}
