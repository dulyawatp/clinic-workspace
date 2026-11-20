import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { getCapability } from "../../domain/capability";
import { Button } from "../../ui/Button";
import { WriteGate } from "../../ui/WriteGate";
import {
  AppointmentBookingError,
  createDepartmentAppointment,
  searchBookingLinks,
  type BookedAppointment,
  type BookingLinkField,
  type BookingLinkOption,
} from "../../transport/native/appointment-booking";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { useCapabilities } from "../shell/CapabilityProvider";

const fields = {
  patient: "appointment.patient",
  company: "appointment.company",
  department: "appointmentWrite.department",
  appointment_type: "appointment.type",
} satisfies Record<BookingLinkField, string>;

const statusKeys = {
  Scheduled: "status.scheduled",
  Open: "appointmentWrite.openStatus",
  Confirmed: "status.confirmed",
  "No Show": "status.noShow",
} satisfies Record<BookedAppointment["status"], string>;

export function AppointmentBooking() {
  const { session, authGeneration } = useSession();
  return nativeTransportEnabled() && session?.mode === "live"
    ? <AppointmentBookingForm key={authGeneration} />
    : null;
}

function AppointmentBookingForm() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const enabled = getCapability(caps, "write.appointment.book") === "enabled";
  const [open, setOpen] = useState(params.get("book") === "1" && enabled);
  const dateId = useId();
  const timeId = useId();
  const [selected, setSelected] = useState<Partial<Record<BookingLinkField, BookingLinkOption>>>({});
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const dispatched = useRef(false);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<AppointmentBookingError | null>(null);
  const [booked, setBooked] = useState<BookedAppointment | null>(null);

  // These links carry a workspace reference (sometimes a fixture alias),
  // not a verified Patient docname. Preserve entry/source, never prefill it.
  const selectPatientAgain = Boolean(params.get("patient"));
  const source = params.get("source");
  const sourceNote =
    source === "return" && params.get("returnSource")
      ? t("appointmentWrite.source.return")
      : source === "checkout"
        ? t("appointmentWrite.source.checkout")
        : null;

  const { patient, company, department, appointment_type } = selected;
  const canSubmit = Boolean(patient && company && department && appointment_type && date && time);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled || dispatched.current || failure?.reason === "uncertain" ||
        !patient || !company || !department || !appointment_type || !date || !time) return;
    dispatched.current = true;
    setSaving(true);
    setFailure(null);
    setBooked(null);
    try {
      const result = await createDepartmentAppointment({
        patient: patient.id,
        company: company.id,
        department: department.id,
        appointment_type: appointment_type.id,
        appointment_date: date,
        appointment_time: time,
      });
      setBooked(result);
      setDate("");
      setTime("");
      setSelected({});
      setOpen(false);
    } catch (error) {
      setFailure(error instanceof AppointmentBookingError ? error : new AppointmentBookingError("uncertain"));
    } finally {
      dispatched.current = false;
      setSaving(false);
      // Best-effort refresh is not reconciliation of an uncertain POST.
      void queryClient.invalidateQueries({ queryKey: ["live-appointments"] });
      void queryClient.invalidateQueries({ queryKey: ["live-appointment"] });
    }
  }

  return (
    <section className="appointment-card appointment-write-card" aria-labelledby="appointment-booking-title">
      <h2 id="appointment-booking-title">{t("appointmentWrite.bookTitle")}</h2>
      <p className="appointment-write-intro">{t("appointmentWrite.bookingScope")}</p>
      {sourceNote !== null && <p className="appointment-write-note" role="status">{sourceNote}</p>}
      {!open && (
        <WriteGate capability="write.appointment.book">
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {t("appointmentWrite.openForm")}
          </Button>
        </WriteGate>
      )}
      {booked && (
        <p role="status" className="appointment-write-success">
          {t("appointmentWrite.booked")}{" "}
          <Link className="live-appointment-link" to={ROUTES.appointment(booked.id)}>{booked.id}</Link>
          {" · "}{t(statusKeys[booked.status])}
        </p>
      )}
      {failure && (
        <p role="alert" className="appointment-write-alert">
          {failure.detail ?? t({
            denied: "appointmentWrite.bookingDenied",
            validation: "appointmentWrite.bookingInvalid",
            uncertain: "appointmentWrite.uncertain",
          }[failure.reason])}
        </p>
      )}
      {open && enabled && (
        <form className="appointment-write-form" onSubmit={(event) => void submit(event)}>
          {selectPatientAgain && <p>{t("appointmentWrite.reselectPatient")}</p>}
          <div className="appointment-form">
            {(Object.keys(fields) as BookingLinkField[]).map((field) => (
              <BookingLink
                key={field}
                field={field}
                selected={selected[field]}
                disabled={saving}
                onSelect={(option) => setSelected((current) => ({ ...current, [field]: option }))}
              />
            ))}
            <label htmlFor={dateId}>
              {t("appointment.date")}
              <input id={dateId} type="date" value={date} required disabled={saving}
                onChange={(event) => setDate(event.target.value)} />
            </label>
            <label htmlFor={timeId}>
              {t("appointment.time")}
              <input id={timeId} type="time" value={time} required disabled={saving}
                onChange={(event) => setTime(event.target.value)} />
            </label>
          </div>
          <div className="appointment-write-actions">
            <Button type="submit" disabled={saving || !canSubmit || failure?.reason === "uncertain"}>
              {t(saving ? "appointmentWrite.saving" : "appointmentWrite.confirmBooking")}
            </Button>
            <Button variant="secondary" disabled={saving} onClick={() => {
              setOpen(false);
              setFailure(null);
            }}>
              {t("appointmentWrite.close")}
            </Button>
          </div>
        </form>
      )}
      {!enabled && !open && <p className="appointment-write-note">{t("appointmentWrite.bookUnavailable")}</p>}
    </section>
  );
}

function BookingLink({
  field, selected, disabled, onSelect,
}: {
  field: BookingLinkField;
  selected?: BookingLinkOption;
  disabled: boolean;
  onSelect: (option?: BookingLinkOption) => void;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const selectId = useId();
  const [search, setSearch] = useState("");
  const [settled, setSettled] = useState("");
  const text = search.trim();
  const matches = useQuery({
    queryKey: ["native-appointment-booking-links", authGeneration, field, settled],
    queryFn: ({ signal }) => searchBookingLinks(field, settled, signal),
    enabled: !disabled && settled === text,
    retry: false,
    gcTime: 0,
  });
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(text), 250);
    return () => window.clearTimeout(timer);
  }, [text]);

  const matchesForText = settled === text ? matches.data ?? [] : [];
  const options = selected && !matchesForText.some((option) => option.id === selected.id)
    ? [selected, ...matchesForText]
    : matchesForText;
  const label = t(fields[field]);
  const loading = settled !== text || matches.isPending;
  return (
    <div className="appointment-write-inline">
      <label htmlFor={selectId}>{label}</label>
      <input
        type="search"
        aria-label={t(`appointmentWrite.search.${field}`)}
        placeholder={t(`appointmentWrite.search.${field}`)}
        autoComplete="off"
        maxLength={100}
        value={search}
        disabled={disabled}
        onChange={(event) => {
          setSearch(event.target.value);
          onSelect();
        }}
      />
      <select id={selectId} value={selected?.id ?? ""} required
        disabled={disabled || loading || matches.isError}
        onChange={(event) => onSelect(options.find((option) => option.id === event.target.value))}>
        <option value="">{t("patientWrite.select")}</option>
        {options.map(({ id, label }) => <option key={id} value={id}>{label === id ? id : `${label} · ${id}`}</option>)}
      </select>
      {!disabled && (
        loading ? <p role="status">{t("common.loading")}</p> :
        matches.isError ? <p role="alert">{t(
          matches.error instanceof AppointmentBookingError && matches.error.reason === "denied"
            ? "appointmentWrite.lookupDenied" : "errors.UPSTREAM_UNAVAILABLE"
        )}</p> :
        options.length === 0 ? <p role="status">{t("appointmentWrite.noMatches")}</p> : null
      )}
    </div>
  );
}
