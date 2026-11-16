import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import type { AppointmentSummary } from "../../domain/appointment";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { mapQueueStatus } from "../../domain/queue";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { AppointmentActions } from "./AppointmentActions";
import { LiveAppointmentPage } from "./LiveAppointmentPage";
import { ReturnVisitLink } from "./ReturnVisitLink";

function Detail({ id }: { id: string }) {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const query = useReadQuery<AppointmentSummary>("frontdesk-appointments", {
    filters: { id },
    limit: 1,
  });
  if (query.isError)
    return (
      <StatePanel
        kind="error"
        title={t(toI18nKey(query.error))}
        onRetry={() => {
          void query.refetch();
        }}
        retryLabel={t("action.retry")}
      />
    );
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const appointment = query.data.items[0];
  if (!appointment)
    return <StatePanel kind="empty" title={t("appointment.notFound")} />;
  const status = mapQueueStatus(appointment.status);
  const fields = [
    ["patient", appointment.patientName],
    ["type", appointment.type],
    ["doctor", appointment.practitionerName],
    ["room", t("appointment.sourceUnavailable")],
    ["company", t("appointment.sourceUnavailable")],
    ["date", appointment.startsAt.slice(0, 10)],
    ["time", appointment.startsAt.slice(11, 16)],
    ["reason", t("appointment.sourceUnavailable")],
    ["notes", t("appointment.sourceUnavailable")],
    [
      "status",
      status.labelKey
        ? t(status.labelKey)
        : `${status.raw} · ${t("status.unmapped")}`,
    ],
  ];
  return (
    <>
      <header className="appointment-card">
        <h2 className="appointment-patient-name">{appointment.patientName}</h2>
        <p className="appointment-patient-id">
          {t("appointment.patientId")}: <span>{appointment.patientId}</span>
        </p>
        <Link to={ROUTES.patient(appointment.patientId)}>
          {t("surface.PAT-03")}
        </Link>
      </header>
      <section className="appointment-card">
        <h2>{t("appointment.detail")}</h2>
        <p className="appointment-notice">{t("appointment.unavailable")}</p>
        <div className="appointment-form">
          {fields.map(([key, value]) => (
            <label key={key}>
              {t(`appointment.${key}`)}
              <input disabled value={value} />
            </label>
          ))}
        </div>
        <AppointmentActions appointment={appointment} caps={caps} />
        <ReturnVisitLink
          patientId={appointment.patientId}
          appointmentId={appointment.id}
        />
      </section>
    </>
  );
}

export function AppointmentPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  return (
    <RequireSession>
      <AddressedAppointment id={id} title={t("appointment.detail")} />
    </RequireSession>
  );
}

function AddressedAppointment({ id, title }: { id?: string; title: string }) {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const capability = getCapability(caps, "read.appointment");
  if (session?.mode === "live")
    return id ? (
      <LiveAppointmentPage id={id} />
    ) : (
      <div className="shell-page-body">
        <StatePanel
          kind="empty"
          title={t("liveAppointments.detailUnavailable")}
        />
      </div>
    );
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (capability === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  return (
    <>
      <PageHeader
        title={title}
        breadcrumb={
          <Link to={ROUTES.appointments}>{t("appointment.calendar")}</Link>
        }
      />
      <div className="shell-page-body">
        {id ? (
          <Detail id={id} />
        ) : (
          <StatePanel kind="empty" title={t("appointment.notFound")} />
        )}
      </div>
    </>
  );
}
