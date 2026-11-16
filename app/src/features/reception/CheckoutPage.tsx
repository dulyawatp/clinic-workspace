import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RequireSession } from "../../app/guards";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { BillingHandoff, type CheckoutVisit } from "./BillingHandoff";
import { CheckoutSamples } from "./CheckoutSamples";

const STATES = [
  "completed",
  "billing_needed",
  "invoiced",
  "paid",
  "partial",
  "blocked",
] as const;

function CheckoutContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const capability = getCapability(caps, "read.billing");
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (capability === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  return <CheckoutView />;
}

function CheckoutView() {
  const { t } = useTranslation();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const filters: Record<string, string> = status ? { handoff: status } : {};
  const query = useReadQuery<CheckoutVisit>("frontdesk-checkouts", {
    filters,
    limit: 20,
    offset: page * 20,
  });
  return (
    <>
      <PageHeader title={t("checkout.title")} breadcrumb={t("nav.reception")} />
      <div className="shell-page-body">
        <section className="reception-card">
          <h2>{t("checkout.title")}</h2>
          <p className="queue-note">{t("checkout.note")}</p>
          <div className="checkout-filter">
            <label>
              {t("checkout.paymentFilter")}
              <select
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">{t("checkout.allStates")}</option>
                {STATES.map((state) => (
                  <option value={state} key={state}>
                    {t(`checkout.state.${state}`)}
                  </option>
                ))}
              </select>
            </label>
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
          ) : query.isPending ||
            query.isFetching ||
            !query.isFetchedAfterMount ? (
            <StatePanel kind="loading" title={t("common.loading")} />
          ) : query.data.items.length === 0 ? (
            <StatePanel kind="empty" title={t("checkout.empty")} />
          ) : (
            <div className="reception-table-wrap">
              <table className="reception-table checkout-table">
                <caption className="shell-sr-only">
                  {t("checkout.title")}
                </caption>
                <thead>
                  <tr>
                    <th id="checkout-patient" scope="col">
                      {t("checkout.patientVisit")}
                    </th>
                    <th id="checkout-handoff" scope="col">
                      {t("checkout.handoff")}
                    </th>
                    <th id="checkout-invoice" scope="col">
                      {t("checkout.invoice")}
                    </th>
                    <th id="checkout-outstanding" scope="col">
                      {t("checkout.outstanding")}
                    </th>
                    <th id="checkout-next" scope="col">
                      {t("queue.nextStep")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.items.map((visit) => (
                    <BillingHandoff key={visit.id} visit={visit} />
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
          <CheckoutSamples />
        </section>
      </div>
    </>
  );
}

export function CheckoutPage() {
  return (
    <RequireSession>
      <CheckoutContent />
    </RequireSession>
  );
}
