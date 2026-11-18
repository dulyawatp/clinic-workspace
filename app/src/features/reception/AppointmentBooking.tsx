import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useSession } from "../../app/SessionProvider";
import { getCapability } from "../../domain/capability";
import { Button } from "../../ui/Button";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import {
  bookNativeAppointment,
  NativeAppointmentWriteError,
} from "../../transport/native/appointment-write";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { useCapabilities } from "../shell/CapabilityProvider";
import { useLivePatients } from "../patients/live-directory";

function messageKey(code: string): string {
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
 * Booking in reception. The native transport is required: there is no proxy
 * booking path, and the wrapper mounts no query when it is unavailable.
 */
export function AppointmentBooking() {
  return nativeTransportEnabled() ? <AppointmentBookingForm /> : null;
}

function AppointmentBookingForm() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const requestedPatient = searchParams.get("patient") ?? "";
  const requestedSource = searchParams.get("source") ?? "";
  const authoredReturnSource = searchParams.get("returnSource") ?? "";
  const autoOpen = searchParams.get("book") === "1";
  const enabled =
    getCapability(caps, "write.appointment.book") === "enabled";
  const appliedRequest = useRef(false);
  const patientId = useId();
  const dateId = useId();
  const timeId = useId();
  const [open, setOpen] = useState(autoOpen && enabled);
  const [selected, setSelected] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [booked, setBooked] = useState<string | null>(null);
  const directory = useLivePatients();

  // Patient choices are the readable directory only. The converted lane lets
  // the server's native write authority decide eligibility, so a readable
  // patient is no longer narrowed by the appointment list.
  const candidates = useMemo(() => directory.data ?? [], [directory.data]);

  useEffect(() => {
    if (selected !== "" && candidates.some((item) => item.workspace_patient_id === selected))
      return;
    setSelected("");
  }, [candidates, selected]);

  useEffect(() => {
    if (appliedRequest.current) return;
    if (requestedPatient === "") {
      appliedRequest.current = true;
      return;
    }
    if (!candidates.some((item) => item.workspace_patient_id === requestedPatient))
      return;
    setSelected(requestedPatient);
    if (autoOpen && enabled) setOpen(true);
    appliedRequest.current = true;
  }, [autoOpen, candidates, enabled, requestedPatient]);

  /*
   * A Demo/Sample claim is only honest when the authored return record is
   * actually on the link. A plain `source=return` is a real booking request
   * opened from an appointment, so it carries no sample claim at all.
   */
  const sourceNote =
    requestedSource === "return" && authoredReturnSource !== ""
      ? t("appointmentWrite.source.return")
      : requestedSource === "checkout"
        ? t("appointmentWrite.source.checkout")
        : null;

  if (session?.mode !== "live") return null;

  const title = t("appointmentWrite.bookTitle");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled || saving) return;
    if (
      !candidates.some((item) => item.workspace_patient_id === selected) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)
    ) {
      setFailure("validation");
      return;
    }
    setFailure(null);
    setBooked(null);
    setSaving(true);
    try {
      const result = await bookNativeAppointment({
        operation_key: crypto.randomUUID(),
        workspace_patient_id: selected,
        appointment_date: date,
        appointment_time: time,
      });
      setBooked(result.appointment.appointment_id);
      setDate("");
      setTime("");
      setSelected("");
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
    try {
      await queryClient.invalidateQueries({ queryKey: ["live-appointments"] });
    } catch {
      // A failed refresh leaves reconciliation to the reader's next view.
    }
    setSaving(false);
  }

  return (
    <section
      className="appointment-card appointment-write-card"
      aria-labelledby="appointment-booking-title"
    >
      <h2 id="appointment-booking-title">{title}</h2>
      <p className="appointment-write-intro">
        {t("appointmentWrite.bookingScope")}
      </p>
      {sourceNote !== null && (
        <p className="appointment-write-note" role="status">
          {sourceNote}
        </p>
      )}
      {!open && (
        <WriteGate capability="write.appointment.book">
          <Button
            variant="secondary"
            disabled={directory.isPending || candidates.length === 0}
            disabledReason={
              candidates.length === 0
                ? t("appointmentWrite.noPatients")
                : undefined
            }
            onClick={() => setOpen(true)}
          >
            {t("appointmentWrite.openForm")}
          </Button>
        </WriteGate>
      )}
      {booked !== null && (
        <p role="status" className="appointment-write-success">
          {t("appointmentWrite.booked", { reference: booked })}
        </p>
      )}
      {!open && directory.isError && (
        <p role="alert" className="appointment-write-alert">
          {t("errors.UPSTREAM_UNAVAILABLE")}
        </p>
      )}
      {failure !== null && (
        <p role="alert" className="appointment-write-alert">
          {t(messageKey(failure))}
        </p>
      )}
      {open && enabled && (
        <form className="appointment-write-form" onSubmit={(event) => void submit(event)} noValidate>
          <p>{t("appointmentWrite.patientRestriction")}</p>
          {directory.isError ? (
            <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
          ) : directory.isPending || !directory.isFetchedAfterMount ? (
            <StatePanel kind="loading" title={t("common.loading")} />
          ) : candidates.length === 0 ? (
            <p role="status">{t("appointmentWrite.noPatients")}</p>
          ) : (
            <div className="appointment-form">
              <label htmlFor={patientId}>
                {t("appointment.patient")}
                <select
                  id={patientId}
                  value={selected}
                  required
                  onChange={(event) => setSelected(event.target.value)}
                >
                  <option value="">{t("patientWrite.select")}</option>
                  {candidates.map((patient) => (
                    <option
                      key={patient.workspace_patient_id}
                      value={patient.workspace_patient_id}
                    >
                      {patient.display_name} · {patient.workspace_patient_id}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={dateId}>
                {t("appointment.date")}
                <input
                  id={dateId}
                  type="date"
                  value={date}
                  required
                  onChange={(event) => setDate(event.target.value)}
                />
              </label>
              <label htmlFor={timeId}>
                {t("appointment.time")}
                <input
                  id={timeId}
                  type="time"
                  value={time}
                  required
                  onChange={(event) => setTime(event.target.value)}
                />
              </label>
            </div>
          )}
          <div className="appointment-write-actions">
            <Button
              type="submit"
              disabled={saving || candidates.length === 0}
            >
              {saving
                ? t("appointmentWrite.saving")
                : t("appointmentWrite.confirmBooking")}
            </Button>
            <Button
              variant="secondary"
              disabled={saving}
              onClick={() => {
                setOpen(false);
                setFailure(null);
              }}
            >
              {t("appointmentWrite.close")}
            </Button>
          </div>
        </form>
      )}
      {!enabled && !open && (
        <p className="appointment-write-note">
          {t("appointmentWrite.bookUnavailable")}
        </p>
      )}
    </section>
  );
}
