import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import type { AppointmentSummary } from "../../domain/appointment";
import { toI18nKey } from "../../domain/errors";
import { mapQueueStatus } from "../../domain/queue";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";
import type { ReceptionScope } from "../reception/scopes";

export function AppointmentScopeList({
  scope,
  date,
}: {
  scope: ReceptionScope;
  date: string;
}) {
  const { t } = useTranslation();
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
  return (
    <>
      <p>
        {date} · {t(`rec.scope.${scope}`)}
      </p>
      <p role="status">{t("patients.total", { total: query.data.total })}</p>
      {query.data.items.length ? (
        <ol className="patients-timeline">
          {query.data.items.map((row) => {
            const status = mapQueueStatus(row.status);
            return (
              <li key={row.id} className="patients-timeline-item">
                <time dateTime={row.startsAt}>
                  {row.startsAt.slice(11, 16)}
                </time>
                <Link to={ROUTES.appointment(row.id)}>
                  {row.patientName} · {row.id}
                </Link>
                <span>
                  {status.labelKey
                    ? t(status.labelKey)
                    : `${status.raw} · ${t("status.unmapped")}`}
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        <StatePanel kind="empty" title={t("rec.appointmentsEmpty")} />
      )}
      {(page > 0 || query.data.total > 20) && (
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
              pages: Math.max(1, Math.ceil(query.data.total / 20)),
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
