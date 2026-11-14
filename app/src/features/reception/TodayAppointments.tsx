import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  type AppointmentSummary,
  canOfferCheckIn,
} from "../../domain/appointment";
import { toI18nKey } from "../../domain/errors";
import { mapQueueStatus } from "../../domain/queue";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useReadQuery } from "../../transport/read/query";
import { Button } from "../../ui/Button";
import { StatePanel } from "../../ui/StatePanel";
import { StatusChip } from "../../ui/StatusChip";
import { WriteGate } from "../../ui/WriteGate";
import type { ReceptionScope } from "./scopes";
import { useReceptionKpis } from "./useReceptionKpis";

export function TodayAppointments({ scope }: { scope: ReceptionScope }) {
  const { t } = useTranslation();
  const summary = useReceptionKpis("today");
  if (summary.isError)
    return <StatePanel kind="error" title={t(toI18nKey(summary.error))} />;
  if (summary.isPending || summary.isFetching || !summary.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const date = summary.data.items[0]?.date;
  if (!date) return <StatePanel kind="empty" title={t("rec.kpisEmpty")} />;
  return (
    <AppointmentPreview key={`${date}-${scope}`} scope={scope} date={date} />
  );
}

function AppointmentPreview({
  scope,
  date,
}: {
  scope: ReceptionScope;
  date: string;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const [page, setPage] = useState(0);
  const query = useReadQuery<AppointmentSummary>("frontdesk-appointments", {
    filters: { date, scope },
    limit: 20,
    offset: page * 20,
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

  const rows = query.data.items;

  if (rows.length === 0)
    return <StatePanel kind="empty" title={t("rec.appointmentsEmpty")} />;

  return (
    <>
      <div className="reception-table-wrap">
        <table className="reception-table">
          <caption className="shell-sr-only">
            {t("rec.appointmentsTitle")}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t("rec.time")}</th>
              <th scope="col">{t("nav.patients")}</th>
              <th scope="col">{t("rec.status")}</th>
              <th scope="col">
                <span className="shell-sr-only">{t("rec.action")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((appointment) => {
              const status = mapQueueStatus(appointment.status);
              return (
                <tr key={appointment.id}>
                  <td data-label={t("rec.time")}>
                    <time dateTime={appointment.startsAt}>
                      {formatDateTime(appointment.startsAt, locale)}
                    </time>
                  </td>
                  <td data-label={t("nav.patients")}>
                    {/* Read-only navigation stays an ordinary router link. */}
                    <Link
                      className="reception-patient"
                      to={ROUTES.patient(appointment.patientId)}
                    >
                      {appointment.patientName}
                    </Link>
                    <small>{appointment.practitionerName}</small>
                  </td>
                  <td data-label={t("rec.status")}>
                    <StatusChip
                      tone="neutral"
                      label={
                        status.labelKey === null
                          ? `${status.raw} · ${t("status.unmapped")}`
                          : t(status.labelKey)
                      }
                    />
                  </td>
                  <td>
                    {canOfferCheckIn(appointment) && (
                      <WriteGate capability="write.appointment.checkin">
                        <Button
                          variant="secondary"
                          disabled
                          disabledReason={t("queue.writeUnavailable")}
                        >
                          {t("action.checkIn")}
                        </Button>
                      </WriteGate>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {query.data.total > 20 && (
        <nav
          aria-label={t("appointment.pages")}
          className="appointment-pagination"
        >
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            {t("appointment.previous")}
          </button>
          <span>
            {t("appointment.pageOf", {
              page: page + 1,
              pages: Math.ceil(query.data.total / 20),
            })}
          </span>
          <button
            type="button"
            disabled={(page + 1) * 20 >= query.data.total}
            onClick={() => setPage(page + 1)}
          >
            {t("appointment.next")}
          </button>
        </nav>
      )}
    </>
  );
}
