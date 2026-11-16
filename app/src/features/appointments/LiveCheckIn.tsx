import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "../../ui/Button";
import { WriteGate } from "../../ui/WriteGate";
import {
  checkinNativeAppointment,
  NativeAppointmentWriteError,
} from "../../transport/native/appointment-write";
import { getCapability } from "../../domain/capability";
import { useCapabilities } from "../shell/CapabilityProvider";
import { useLiveAppointment } from "./live-appointment";
import { useAppointmentWriteLock } from "./useAppointmentWriteLock";

function failureKey(code: string): string {
  switch (code) {
    case "stale":
      return "appointmentWrite.stale";
    case "conflict":
      return "appointmentWrite.conflict";
    case "validation":
      return "appointmentWrite.invalid";
    case "forbidden":
    case "auth":
      return "appointmentWrite.forbidden";
    default:
      return "appointmentWrite.uncertain";
  }
}

/**
 * Check-in eligibility comes only from the versioned server detail
 * (`can_checkin`), never from a local status or date rule. Unknown, loading or
 * failed eligibility shows no check-in action.
 */
export function LiveCheckIn({ appointmentId }: { appointmentId: string }) {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const queryClient = useQueryClient();
  const enabled = getCapability(caps, "write.appointment.checkin") === "enabled";
  const detail = useLiveAppointment(enabled ? appointmentId : undefined);
  const { locked, refreshLock } = useAppointmentWriteLock();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checkedIn, setCheckedIn] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (!enabled) return null;
  if (checkedIn)
    return (
      <p role="status" className="appointment-write-success">
        {t("appointmentWrite.checkedIn")}
      </p>
    );
  if (detail.isPending || detail.isFetching)
    return <span className="appointment-write-muted">{t("common.loading")}</span>;
  if (detail.isError)
    return (
      <span className="appointment-write-muted">
        {t("appointmentWrite.eligibilityUnavailable")}
      </span>
    );
  if (!detail.data.can_checkin)
    return (
      <span className="appointment-write-muted">
        {t("appointmentWrite.checkinUnavailable")}
      </span>
    );

  async function submit() {
    if (saving || locked || detail.data?.can_checkin !== true) return;
    setSaving(true);
    setFailure(null);
    try {
      await checkinNativeAppointment({
        operation_key: crypto.randomUUID(),
        workspace_appointment_id: appointmentId,
        base_modified: detail.data.modified,
      });
      setCheckedIn(true);
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["live-appointments"] });
      void queryClient.invalidateQueries({ queryKey: ["live-appointment"] });
    } catch (error) {
      setFailure(
        error instanceof NativeAppointmentWriteError ? error.code : "uncertain",
      );
    } finally {
      refreshLock();
      setSaving(false);
    }
  }

  return (
    <div className="appointment-write-inline">
      {!open && (
        <WriteGate capability="write.appointment.checkin">
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {t("action.checkIn")}
          </Button>
        </WriteGate>
      )}
      {open && (
        <div role="group" aria-label={t("appointmentWrite.checkinConfirmLabel")}>
          <p>
            {t("appointmentWrite.checkinConfirm", {
              reference: appointmentId,
            })}
          </p>
          <div className="appointment-write-actions">
            <Button
              disabled={saving || locked}
              disabledReason={locked ? t("appointmentWrite.locked") : undefined}
              onClick={() => void submit()}
            >
              {saving ? t("appointmentWrite.saving") : t("action.checkIn")}
            </Button>
            <Button
              variant="secondary"
              disabled={saving}
              onClick={() => setOpen(false)}
            >
              {t("appointmentWrite.close")}
            </Button>
          </div>
        </div>
      )}
      {locked && (
        <p role="alert" className="appointment-write-alert">
          {t("appointmentWrite.locked")}
        </p>
      )}
      {failure !== null && failure !== "uncertain" && (
        <p role="alert" className="appointment-write-alert">
          {t(failureKey(failure))}
        </p>
      )}
    </div>
  );
}
