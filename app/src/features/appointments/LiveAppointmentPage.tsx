import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ROUTES } from "../../app/routes";
import { toI18nKey } from "../../domain/errors";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import {
  NativeAppointmentWriteError,
  rescheduleNativeAppointment,
} from "../../transport/native/appointment-write";
import { useLiveAppointment } from "./live-appointment";
import { ReturnVisitLink } from "./ReturnVisitLink";
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

export function LiveAppointmentPage({ id }: { id: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const detail = useLiveAppointment(id);
  const { locked, refreshLock } = useAppointmentWriteLock();
  const unavailableId = useId();
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  if (detail.isPending)
    return (
      <div className="shell-page-body">
        <StatePanel kind="loading" title={t("common.loading")} />
      </div>
    );
  if (detail.isError || !detail.data)
    return (
      <div className="shell-page-body">
        <StatePanel
          kind="error"
          title={t(toI18nKey(detail.error ?? { code: "UNKNOWN", retryable: false }))}
          onRetry={() => {
            void detail.refetch();
          }}
          retryLabel={t("action.retry")}
        />
      </div>
    );

  const appointment = detail.data;
  const canReschedule = appointment.can_reschedule;

  function openForm() {
    setDate(appointment.appointment_date);
    setTime(appointment.appointment_time.slice(0, 5));
    setFailure(null);
    setSuccess(null);
    setOpen(true);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || locked) return;
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)
    ) {
      setFailure("validation");
      return;
    }
    setFailure(null);
    setSuccess(null);
    setSaving(true);
    try {
      const result = await rescheduleNativeAppointment({
        operation_key: crypto.randomUUID(),
        workspace_appointment_id: id,
        base_modified: appointment.modified,
        appointment_date: date,
        appointment_time: time,
      });
      setSuccess(
        t("appointmentWrite.rescheduled", {
          date: result.appointment.appointment_date,
          time: result.appointment.appointment_time,
        }),
      );
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
    <>
      <PageHeader
        title={t("appointment.detail")}
        breadcrumb={
          <Link to={ROUTES.appointments}>{t("appointment.calendar")}</Link>
        }
      />
      <div className="shell-page-body">
        <section className="appointment-card" aria-labelledby="live-detail-title">
          <h2 id="live-detail-title" className="appointment-live-patient">
            {appointment.workspace_patient_id}
          </h2>
          <p
            className="appointment-patient-id appointment-live-reference"
            style={{ minWidth: 0 }}
          >
            {t("liveAppointments.reference")}:{" "}
            <span
              className="appointment-patient-id-value"
              data-testid="appointment-reference"
              style={{ minWidth: 0, overflowWrap: "anywhere" }}
            >
              {appointment.appointment_id}
            </span>
          </p>
          <dl className="appointment-detail-list">
            <div>
              <dt>{t("appointment.date")}</dt>
              <dd>{appointment.appointment_date}</dd>
            </div>
            <div>
              <dt>{t("appointment.time")}</dt>
              <dd>{appointment.appointment_time}</dd>
            </div>
            <div>
              <dt>{t("appointment.status")}</dt>
              <dd>{appointment.status}</dd>
            </div>
            <div>
              <dt>{t("appointmentWrite.version")}</dt>
              <dd>{appointment.modified}</dd>
            </div>
          </dl>

          <div className="appointment-actions">
            {!canReschedule ? (
              <p className="appointment-write-note" role="status">
                {t("appointmentWrite.rescheduleUnavailable")}
              </p>
            ) : (
              <WriteGate capability="write.appointment.reschedule">
                <Button
                  variant="secondary"
                  disabled={locked}
                  disabledReason={
                    locked ? t("appointmentWrite.locked") : undefined
                  }
                  onClick={openForm}
                >
                  {t("action.reschedule")}
                </Button>
              </WriteGate>
            )}
            <ReturnVisitLink
              patientId={appointment.workspace_patient_id}
              appointmentId={appointment.appointment_id}
            />
          </div>

          <div className="appointment-actions">
            <Button
              variant="secondary"
              disabled
              aria-describedby={unavailableId}
            >
              {t("appointment.cancel")}
            </Button>
            <Button
              variant="secondary"
              disabled
              aria-describedby={unavailableId}
            >
              {t("appointment.noShow")}
            </Button>
          </div>
          <p id={unavailableId} className="appointment-write-note">
            {t("appointmentWrite.cancelNoShowUnavailable")}
          </p>

          {locked && (
            <p role="alert" className="appointment-write-alert">
              {t("appointmentWrite.locked")}
            </p>
          )}
          {success !== null && (
            <p role="status" className="appointment-write-success">
              {success}
            </p>
          )}
          {failure !== null && (
            <div role="alert" className="appointment-write-alert">
              <p>{t(failureKey(failure))}</p>
              <Button variant="secondary" onClick={() => void detail.refetch()}>
                {t("appointmentWrite.reload")}
              </Button>
            </div>
          )}

          {open && canReschedule && (
            <form
              className="appointment-write-form"
              onSubmit={(event) => void submit(event)}
              noValidate
            >
              <h3>{t("action.reschedule")}</h3>
              <p>{t("appointmentWrite.rescheduleScope")}</p>
              <div className="appointment-form">
                <label>
                  {t("appointment.date")}
                  <input
                    type="date"
                    value={date}
                    required
                    onChange={(event) => setDate(event.target.value)}
                  />
                </label>
                <label>
                  {t("appointment.time")}
                  <input
                    type="time"
                    value={time}
                    required
                    onChange={(event) => setTime(event.target.value)}
                  />
                </label>
              </div>
              <div className="appointment-write-actions">
                <Button
                  type="submit"
                  disabled={saving || locked}
                  disabledReason={
                    locked ? t("appointmentWrite.locked") : undefined
                  }
                >
                  {saving
                    ? t("appointmentWrite.saving")
                    : t("appointmentWrite.confirmReschedule")}
                </Button>
                <Button
                  variant="secondary"
                  disabled={saving}
                  onClick={() => setOpen(false)}
                >
                  {t("appointmentWrite.close")}
                </Button>
              </div>
            </form>
          )}
        </section>
      </div>
    </>
  );
}
