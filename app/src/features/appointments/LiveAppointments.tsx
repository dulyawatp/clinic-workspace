import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useSession } from "../../app/SessionProvider";
import { ROUTES } from "../../app/routes";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { LiveCheckIn } from "./LiveCheckIn";
import { useLiveAppointments } from "./live-appointments";

function LiveRows({ checkinEnabled }: { checkinEnabled: boolean }) {
  const { t } = useTranslation();
  const { refresh } = useSession();
  const query = useLiveAppointments();
  if (query.isError) {
    if (query.error.code === "AUTH_EXPIRED")
      return (
        <StatePanel
          kind="error"
          title={t(toI18nKey(query.error))}
          onRetry={() => {
            void refresh();
          }}
          retryLabel={t("action.retry")}
        />
      );
    if (query.error.retryable)
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
    return <StatePanel kind="error" title={t(toI18nKey(query.error))} />;
  }
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (!query.data.length)
    return <StatePanel kind="empty" title={t("liveAppointments.empty")} />;
  return (
    <div className="live-appointments-table-wrap">
      <table className="live-appointments-table">
        <caption className="shell-sr-only">
          {t("liveAppointments.title")}
        </caption>
        <thead>
          <tr>
            {["date", "time", "patient", "reference", "status"].map((field) => (
              <th key={field} scope="col">
                {t(`liveAppointments.${field}`)}
              </th>
            ))}
            {checkinEnabled && (
              <th scope="col">
                <span className="shell-sr-only">
                  {t("appointmentWrite.actions")}
                </span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {query.data.map((item) => (
            <tr key={item.appointment_id}>
              <td data-label={t("liveAppointments.date")}>
                {item.appointment_date}
              </td>
              <td data-label={t("liveAppointments.time")}>
                {item.appointment_time}
              </td>
              <td data-label={t("liveAppointments.patient")}>
                {item.workspace_patient_id}
              </td>
              <td data-label={t("liveAppointments.reference")}>
                <Link
                  className="live-appointment-link"
                  to={ROUTES.appointment(item.appointment_id)}
                >
                  {item.appointment_id}
                </Link>
              </td>
              <td data-label={t("liveAppointments.status")}>
                <span className="live-appointment-status">{item.status}</span>
              </td>
              {checkinEnabled && (
                <td data-label={t("appointmentWrite.actions")}>
                  <LiveCheckIn appointmentId={item.appointment_id} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LiveAppointments() {
  const { t } = useTranslation();
  const { session } = useSession();
  const { caps } = useCapabilities();
  const capability = getCapability(caps, "read.appointment");
  const checkinEnabled =
    getCapability(caps, "write.appointment.checkin") === "enabled";
  if (
    !session?.user.roles.some((role) => role === "REC" || role === "DOC") ||
    capability === "denied" ||
    capability === "blocked_unverified"
  )
    return (
      <div className="shell-page-body">
        <StatePanel kind="permission-denied" title={t("state.denied.title")} />
      </div>
    );
  if (capability === "error_upstream")
    return (
      <div className="shell-page-body">
        <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
      </div>
    );
  return (
    <>
      <PageHeader title={t("liveAppointments.title")} />
      <div className="shell-page-body">
        <section
          className="appointment-card live-appointments"
          aria-labelledby="live-appointments-title"
        >
          <div className="live-appointments-heading">
            <div>
              <p className="live-appointments-eyebrow">
                {t("liveAppointments.live")}
              </p>
              <h2 id="live-appointments-title">
                {t("liveAppointments.title")}
              </h2>
              <p>{t("liveAppointments.description")}</p>
            </div>
          </div>
          <p className="appointment-notice">{t("liveAppointments.readOnly")}</p>
          <LiveRows checkinEnabled={checkinEnabled} />
        </section>
      </div>
    </>
  );
}
