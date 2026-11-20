import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { toI18nKey } from "../../domain/errors";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import {
  cancelNativeAppointment,
  NativeAppointmentWriteError,
  noShowNativeAppointment,
  rescheduleNativeAppointment,
} from "../../transport/native/appointment-write";
import { useLiveAppointment } from "./live-appointment";
import { NativeAppointmentCheckIn } from "./NativeAppointmentCheckIn";
import { ReturnVisitLink } from "./ReturnVisitLink";
import { NativeLabReportPanel } from "../clinical/NativeLabReports";

const TERMINAL_STATUSES = new Set(["Closed", "Cancelled", "No Show", "Checked Out"]);

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
  const { session } = useSession();
  const queryClient = useQueryClient();
  const detail = useLiveAppointment(id);
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [transition, setTransition] = useState<"cancel" | "no_show" | null>(
    null,
  );
  const [reason, setReason] = useState("");

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
  // Display only: a terminal row has no designed source transition left. The
  // server remains the authority and refuses every ineligible target.
  const transitionUnavailable = TERMINAL_STATUSES.has(appointment.status);

  /** Best-effort record refresh; it never changes a decided write outcome. */
  async function refreshRecords() {
    try {
      await queryClient.invalidateQueries({ queryKey: ["live-appointments"] });
      await queryClient.invalidateQueries({ queryKey: ["live-appointment"] });
    } catch {
      // A failed refresh leaves reconciliation to the reader's next view.
    }
  }

  function openForm() {
    setDate(appointment.appointment_date);
    setTime(appointment.appointment_time.slice(0, 5));
    setFailure(null);
    setSuccess(null);
    setOpen(true);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
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
    } catch (error) {
      // A deterministic refusal is a known failure; anything else (lost,
      // unreadable) is an unknown outcome. Nothing is retried automatically.
      setFailure(
        error instanceof NativeAppointmentWriteError ? error.code : "uncertain",
      );
    }
    // Best-effort refresh only: it is not a reconciliation guarantee, and an
    // unknown outcome still requires the reader to check current records.
    await refreshRecords();
    setSaving(false);
  }

  function openTransition(kind: "cancel" | "no_show") {
    setTransition(kind);
    setReason("");
    setFailure(null);
    setSuccess(null);
  }

  /**
   * One-shot cancel/no-show submit. The confirmation step is explicit, the
   * in-flight guard blocks a double submit, and the reason is required: the
   * server records it verbatim on the appointment.
   */
  async function submitTransition(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || transition === null) return;
    if (reason.trim() === "" || reason.trim() !== reason) {
      setFailure("validation");
      return;
    }
    setFailure(null);
    setSuccess(null);
    setSaving(true);
    const input = {
      operation_key: crypto.randomUUID(),
      patient: appointment.workspace_patient_id,
      appointment_id: id,
      reason,
    };
    try {
      if (transition === "cancel") await cancelNativeAppointment(input);
      else await noShowNativeAppointment(input);
      setSuccess(
        t(
          transition === "cancel"
            ? "appointmentWrite.cancelled"
            : "appointmentWrite.noShowRecorded",
        ),
      );
      setTransition(null);
      setReason("");
    } catch (error) {
      // A deterministic refusal is a known failure; anything else (lost,
      // unreadable) is an unknown outcome. Nothing is retried automatically.
      setFailure(
        error instanceof NativeAppointmentWriteError ? error.code : "uncertain",
      );
    }
    // Best-effort refresh only: it is not a reconciliation guarantee, and an
    // unknown outcome still requires the reader to check current records.
    await refreshRecords();
    setSaving(false);
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
            {session?.user.roles.includes("REC") && appointment.is_fixture === false &&
              appointment.native_appointment_id && (
                <NativeAppointmentCheckIn
                  key={appointment.native_appointment_id}
                  nativeId={appointment.native_appointment_id}
                />
              )}
            {appointment.is_fixture === false && <Link className="pc-button pc-button--primary" to={ROUTES.linkedVisit(appointment.appointment_id)}>
              {t("workspace.openEncounter")}
            </Link>}
            {!canReschedule ? (
              <p className="appointment-write-note" role="status">
                {t("appointmentWrite.rescheduleUnavailable")}
              </p>
            ) : (
              <WriteGate capability="write.appointment.reschedule">
                <Button variant="secondary" onClick={openForm}>
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
            {transitionUnavailable || transition !== null ? null : (
              <>
                <WriteGate capability="write.appointment.cancel">
                  <Button
                    variant="secondary"
                    onClick={() => openTransition("cancel")}
                  >
                    {t("appointment.cancel")}
                  </Button>
                </WriteGate>
                <WriteGate capability="write.appointment.no_show">
                  <Button
                    variant="secondary"
                    onClick={() => openTransition("no_show")}
                  >
                    {t("appointment.noShow")}
                  </Button>
                </WriteGate>
              </>
            )}
          </div>
          {transitionUnavailable && transition === null && (
            <p className="appointment-write-note" role="status">
              {t("appointmentWrite.actionUnavailable")}
            </p>
          )}
          {transition !== null && (
            <form
              className="appointment-write-form"
              role="group"
              aria-label={t(
                transition === "cancel"
                  ? "appointmentWrite.cancelConfirmLabel"
                  : "appointmentWrite.noShowConfirmLabel",
              )}
              onSubmit={(event) => void submitTransition(event)}
              noValidate
            >
              <p>
                {t(
                  transition === "cancel"
                    ? "appointmentWrite.cancelConfirm"
                    : "appointmentWrite.noShowConfirm",
                  { reference: appointment.appointment_id },
                )}
              </p>
              <label>
                {t("appointmentWrite.reasonLabel")}
                <textarea
                  value={reason}
                  maxLength={200}
                  required
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <div className="appointment-write-actions">
                <Button
                  type="submit"
                  disabled={saving || reason.trim() === ""}
                  disabledReason={
                    reason.trim() === ""
                      ? t("appointmentWrite.reasonRequired")
                      : undefined
                  }
                >
                  {saving
                    ? t("appointmentWrite.saving")
                    : t(
                        transition === "cancel"
                          ? "appointmentWrite.cancelConfirmLabel"
                          : "appointmentWrite.noShowConfirmLabel",
                      )}
                </Button>
                <Button
                  variant="secondary"
                  disabled={saving}
                  onClick={() => {
                    setTransition(null);
                    setReason("");
                  }}
                >
                  {t("appointmentWrite.close")}
                </Button>
              </div>
            </form>
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
                  disabled={saving}
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
        {appointment.is_fixture === false && (
          <section className="appointment-card" aria-labelledby="appointment-lab-reports">
            <h2 id="appointment-lab-reports">{t("labReports.title")}</h2>
            <NativeLabReportPanel context={{ kind: "appointment", id: appointment.appointment_id }}
              uploadAppointmentRef={appointment.appointment_id} />
          </section>
        )}
      </div>
    </>
  );
}
