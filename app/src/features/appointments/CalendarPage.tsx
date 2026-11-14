import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { isReceptionScope } from "../reception/scopes";
import { useCapabilities } from "../shell/CapabilityProvider";
import { AppointmentScopeList } from "./AppointmentScopeList";
import { AvailabilityLegend } from "./AvailabilityLegend";
import { CalendarGrid } from "./CalendarGrid";
import { LiveAppointments } from "./LiveAppointments";

interface AvailabilityOptions {
  practitioners: Array<{ id: string; name: string }>;
  types: string[];
}

function CalendarContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const capability = getCapability(caps, "read.appointment");
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (capability === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  if (session?.mode === "live") return <LiveAppointments />;
  return <CalendarView />;
}

function CalendarView() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const scope = params.get("scope");
  const scopeDate = params.get("date");
  const [view, setView] = useState<"day" | "week" | "list">("day");
  const [date, setDate] = useState("2026-11-05");
  const [doctorInput, setDoctorInput] = useState("");
  const [appointmentType, setAppointmentType] = useState("");
  const query = useReadQuery<AvailabilityOptions>(
    "frontdesk-availability-options",
    { limit: 1 },
  );
  const options = query.data?.items[0];
  const practitioners = (options?.practitioners ?? []).map((doctor) => ({
    ...doctor,
    label:
      options?.practitioners.filter((other) => other.name === doctor.name)
        .length === 1
        ? doctor.name
        : `${doctor.name} — ${doctor.id}`,
  }));
  const matchingDoctors = practitioners.filter(
    (doctor) => doctor.label === doctorInput,
  );
  const practitionerId =
    matchingDoctors.length === 1 ? (matchingDoctors[0]?.id ?? "") : "";
  const unresolved = doctorInput !== "" && practitionerId === "";
  if (scope !== null)
    return (
      <>
        <PageHeader
          title={t("appointment.calendar")}
          breadcrumb={<Link to={ROUTES.reception}>{t("nav.reception")}</Link>}
        />
        <div className="shell-page-body">
          <section className="appointment-card">
            <Link to={ROUTES.appointments}>{t("appointment.calendar")}</Link>
            {isReceptionScope(scope) &&
            scopeDate &&
            /^\d{4}-\d{2}-\d{2}$/.test(scopeDate) ? (
              <AppointmentScopeList
                key={`${scope}-${scopeDate}`}
                scope={scope}
                date={scopeDate}
              />
            ) : (
              <StatePanel kind="error" title={t("errors.VALIDATION")} />
            )}
          </section>
        </div>
      </>
    );
  return (
    <>
      <PageHeader
        title={t("appointment.calendar")}
        breadcrumb={t("nav.reception")}
      />
      <div className="shell-page-body">
        <section className="appointment-card">
          <h2>{t("appointment.calendar")}</h2>
          <p className="appointment-notice">{t("appointment.sample")}</p>
          <div className="appointment-filters">
            <label>
              {t("appointment.date")}
              <input
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <label>
              {t("appointment.doctor")}
              <input
                list="appointment-doctors"
                value={doctorInput}
                onChange={(event) => setDoctorInput(event.target.value)}
                placeholder={t("appointment.allDoctors")}
                aria-invalid={unresolved}
                aria-describedby={
                  unresolved ? "appointment-doctor-error" : undefined
                }
                disabled={!options}
              />
            </label>
            <datalist id="appointment-doctors">
              {practitioners.map((doctor) => (
                <option key={doctor.id} value={doctor.label} />
              ))}
            </datalist>
            <label>
              {t("appointment.type")}
              <select
                value={appointmentType}
                onChange={(event) => setAppointmentType(event.target.value)}
                disabled={!options}
              >
                <option value="">{t("appointment.allTypes")}</option>
                {(options?.types ?? []).map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <span>{t("appointment.view")}</span>
              <div className="appointment-view">
                {(["day", "week", "list"] as const).map((item) => (
                  <button
                    type="button"
                    key={item}
                    aria-pressed={view === item}
                    onClick={() => setView(item)}
                  >
                    {t(`appointment.${item}`)}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <AvailabilityLegend />
          {query.isError ? (
            <StatePanel
              kind="error"
              title={t(toI18nKey(query.error))}
              onRetry={() => {
                void query.refetch();
              }}
              retryLabel={t("action.retry")}
            />
          ) : query.isPending ||
            query.isFetching ||
            !query.isFetchedAfterMount ? (
            <StatePanel kind="loading" title={t("common.loading")} />
          ) : !options ? (
            <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
          ) : unresolved ? (
            <p role="alert" id="appointment-doctor-error">
              {t("appointment.doctorUnresolved")}
            </p>
          ) : (
            <CalendarGrid
              key={`${date}-${view}-${practitionerId}-${appointmentType}`}
              view={view}
              date={date}
              practitionerId={practitionerId}
              appointmentType={appointmentType}
            />
          )}
        </section>
      </div>
    </>
  );
}

export function CalendarPage() {
  return (
    <RequireSession>
      <CalendarContent />
    </RequireSession>
  );
}
