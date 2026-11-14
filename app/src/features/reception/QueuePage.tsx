import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import {
  isQueueStatus,
  mapQueueStatus,
  QUEUE_STATUSES,
} from "../../domain/queue";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useReadQuery } from "../../transport/read/query";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import { useCapabilities } from "../shell/CapabilityProvider";
import { type QueueEntry, QueueRow } from "./QueueRow";

interface QueueKpis {
  date: string;
  asOf: string;
  appointments: number;
  arrivals: number;
  waiting: number;
  inConsultation: number;
}
const KPIS = [
  { key: "appointments", status: "", label: "queue.appointments" },
  { key: "arrivals", status: "arrived", label: "status.arrived" },
  { key: "waiting", status: "waiting", label: "status.waiting" },
  {
    key: "inConsultation",
    status: "in_consultation",
    label: "status.withDoctor",
  },
] as const;

function QueueContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const state = getCapability(caps, "read.appointment");
  if (state === "denied" || state === "blocked_unverified")
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (state === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  return <QueueView />;
}

function QueueView() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { caps } = useCapabilities();
  const [params, setParams] = useSearchParams();
  const rawStatus = params.get("status");
  const status = isQueueStatus(rawStatus) ? rawStatus : "";
  const [search, setSearch] = useState("");
  const [paging, setPaging] = useState({ status: "", page: 0 });
  const page = paging.status === status ? paging.page : 0;
  function setPage(next: number) {
    setPaging({ status, page: next });
  }
  useEffect(() => {
    const safe = new URLSearchParams(status ? { status } : {});
    if (params.toString() !== safe.toString())
      setParams(safe, { replace: true });
  }, [params, setParams, status]);
  function setStatus(next: string) {
    setParams(isQueueStatus(next) ? { status: next } : {});
  }
  const kpis = useReadQuery<QueueKpis>("frontdesk-queue-kpis", { limit: 1 });
  if (kpis.isError)
    return (
      <StatePanel
        kind="error"
        title={t(toI18nKey(kpis.error))}
        onRetry={() => {
          void kpis.refetch();
        }}
        retryLabel={t("action.retry")}
      />
    );
  if (kpis.isPending || kpis.isFetching || !kpis.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const summary = kpis.data.items[0];
  if (!summary)
    return <StatePanel kind="empty" title={t("queue.summaryUnavailable")} />;
  return (
    <>
      <PageHeader
        title={t("queue.title")}
        breadcrumb={<Link to={ROUTES.reception}>{t("nav.reception")}</Link>}
      />
      <div className="shell-page-body">
        <section className="reception-card queue-card">
          <div className="queue-heading">
            <div>
              <h2>{t("queue.title")}</h2>
              <p>
                {t("queue.sampleDate", { date: summary.date })} ·{" "}
                {t("queue.asOf", {
                  time: formatDateTime(summary.asOf, locale),
                })}
              </p>
            </div>
            <div className="queue-write-action">
              <WriteGate
                capability="write.appointment.book"
                reason={t("queue.writeUnavailable")}
              >
                <Button
                  disabled
                  disabledReason={
                    getCapability(caps, "write.appointment.book") === "enabled"
                      ? t("queue.writeUnavailable")
                      : undefined
                  }
                >
                  {t("queue.walkIn")}
                </Button>
              </WriteGate>
            </div>
          </div>
          <p className="queue-note">{t("queue.mappingNote")}</p>
          <div className="queue-kpis">
            {KPIS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="reception-kpi"
                aria-pressed={status === item.status}
                onClick={() => {
                  setStatus(item.status);
                  setSearch("");
                  setPage(0);
                }}
              >
                <span>{t(item.label)}</span>
                <strong>{summary[item.key]}</strong>
                <small>{t("queue.todaySample")}</small>
              </button>
            ))}
          </div>
          <div className="queue-filters">
            <label>
              {t("queue.search")}
              <input
                type="search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
              />
            </label>
            <label>
              {t("rec.status")}
              <select
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">{t("queue.allStatuses")}</option>
                {QUEUE_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {t(mapQueueStatus(value).labelKey ?? value)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <QueueResults
            date={summary.date}
            search={search}
            status={status}
            page={page}
            setPage={setPage}
            refreshSummary={() => {
              void kpis.refetch();
            }}
          />
        </section>
      </div>
    </>
  );
}

function QueueResults({
  date,
  search,
  status,
  page,
  setPage,
  refreshSummary,
}: {
  date: string;
  search: string;
  status: string;
  page: number;
  setPage: (page: number) => void;
  refreshSummary: () => void;
}) {
  const { t } = useTranslation();
  const filters: Record<string, string> = { date };
  if (status) filters.status = status;
  const query = useReadQuery<QueueEntry>("frontdesk-queue", {
    q: search,
    filters,
    limit: 20,
    offset: page * 20,
  });
  return (
    <>
      <div className="queue-refresh">
        <span>{t("queue.fixtureOnly")}</span>
        <button
          type="button"
          onClick={() => {
            refreshSummary();
            void query.refetch();
          }}
        >
          {t("queue.refresh")}
        </button>
      </div>
      {query.isError ? (
        <StatePanel
          kind="error"
          title={t(toI18nKey(query.error))}
          onRetry={() => {
            void query.refetch();
          }}
          retryLabel={t("action.retry")}
        />
      ) : query.isPending || query.isFetching || !query.isFetchedAfterMount ? (
        <StatePanel kind="loading" title={t("common.loading")} />
      ) : query.data.items.length === 0 ? (
        <StatePanel kind="empty" title={t("queue.empty")} />
      ) : (
        <div className="reception-table-wrap">
          <table className="reception-table queue-table">
            <caption className="shell-sr-only">{t("queue.title")}</caption>
            <thead>
              <tr>
                <th id="queue-header-time" scope="col">
                  {t("rec.time")}
                </th>
                <th id="queue-header-patient" scope="col">
                  {t("nav.patients")}
                </th>
                <th id="queue-header-doctor" scope="col">
                  {t("queue.doctorType")}
                </th>
                <th id="queue-header-reason" scope="col">
                  {t("queue.reason")}
                </th>
                <th id="queue-header-arrival" scope="col">
                  {t("queue.arrivalWait")}
                </th>
                <th id="queue-header-status" scope="col">
                  {t("rec.status")}
                </th>
                <th id="queue-header-next" scope="col">
                  {t("queue.nextStep")}
                </th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <QueueRow key={row.id} appointment={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {query.data && (page > 0 || (page + 1) * 20 < query.data.total) && (
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

export function QueuePage() {
  return (
    <RequireSession>
      <QueueContent />
    </RequireSession>
  );
}
