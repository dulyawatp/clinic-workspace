import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import { LiveAppointments } from "../appointments/LiveAppointments";
import { useCapabilities } from "../shell/CapabilityProvider";
import { KpiCard } from "./KpiCard";
import { PatientRegistration } from "./PatientRegistration";
import { isReceptionScope, type ReceptionScope, SCOPE_BY_KPI } from "./scopes";
import { TodayAppointments } from "./TodayAppointments";
import { type KpiRange, useReceptionKpis } from "./useReceptionKpis";

/** Fixed label keys: an unknown server `key` is skipped, never guessed. */
const KPI_LABELS: Readonly<Record<string, string>> = {
  todayAppointments: "rec.total",
  checkedIn: "v3.checked",
  waiting: "rec.waiting",
  upcoming: "v3.upcoming",
};

/** Only the documented periods get a scope label; anything else shows none. */
const SCOPE_LABELS: Readonly<Record<string, string>> = {
  today: "v3.today",
  week: "v3.week",
  month: "v3.month",
};

function KpiGrid({ range }: { range: KpiRange }) {
  const { t } = useTranslation();
  const query = useReceptionKpis(range);

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

  const visible = query.data.items.filter(
    (kpi) =>
      Object.hasOwn(KPI_LABELS, kpi.key) &&
      Object.hasOwn(SCOPE_BY_KPI, kpi.key) &&
      /^\d{4}-\d{2}-\d{2}$/.test(kpi.date) &&
      Number.isSafeInteger(kpi.count) &&
      kpi.count >= 0,
  );
  if (visible.length === 0)
    return <StatePanel kind="empty" title={t("rec.kpisEmpty")} />;

  return (
    <ul className="reception-kpis">
      {visible.map((kpi) => (
        <li key={kpi.id}>
          <KpiCard
            testId={`kpi-${kpi.key}`}
            labelKey={KPI_LABELS[kpi.key] ?? "rec.total"}
            value={kpi.count}
            scopeKey={SCOPE_LABELS[kpi.scope]}
            href={
              kpi.key === "waiting"
                ? `${ROUTES.queue}?status=waiting`
                : `${ROUTES.appointments}?scope=${SCOPE_BY_KPI[kpi.key]}&date=${encodeURIComponent(kpi.date)}`
            }
          />
        </li>
      ))}
    </ul>
  );
}

function ReceptionContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session, authGeneration } = useSession();
  const [params] = useSearchParams();
  const requested = params.get("scope");
  const scope: ReceptionScope = isReceptionScope(requested) ? requested : "all";

  const capability = getCapability(caps, "read.appointment");
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <div className="shell-page-body">
        <StatePanel
          kind="permission-denied"
          title={t("state.denied.title")}
          detail={t("state.denied.body", { resource: t("surface.REC-01") })}
        />
      </div>
    );
  if (capability === "error_upstream")
    return (
      <div className="shell-page-body">
        <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
      </div>
    );

  // Checkout is a REC-05 destination: the tile exists only once payment is
  // actually permitted, and even then it stays disabled until that surface
  // ships. A hidden tile is never a substitute for the proxy check.
  const paymentEnabled =
    getCapability(caps, "write.payment.receive") === "enabled";
  const billingCapability = getCapability(caps, "read.billing");

  return (
    <>
      <PageHeader title={t("surface.REC-01")} />
      <div className="shell-page-body">
        <section aria-labelledby="reception-tiles-title">
          <h2 id="reception-tiles-title">{t("rec.quick")}</h2>
          <ul className="reception-tiles">
            <li>
              {session?.mode === "live" &&
              session.user.roles.includes("REC") &&
              getCapability(caps, "write.patient.create") === "enabled" ? (
                <PatientRegistration key={authGeneration} />
              ) : (
                <WriteGate
                  capability="write.patient.create"
                  reason={t("patients.registerBlocked")}
                >
                  <Button
                    disabled
                    disabledReason={t("patients.registerBlocked")}
                  >
                    {t("action.register")}
                  </Button>
                </WriteGate>
              )}
            </li>
            <li>
              <WriteGate capability="write.appointment.book">
                <Button>{t("action.book")}</Button>
              </WriteGate>
            </li>
            <li>
              <WriteGate capability="write.photo.upload">
                <Button>{t("rec.photos")}</Button>
              </WriteGate>
            </li>
            <li>
              <Link className="reception-tile-link" to={ROUTES.patients}>
                {t("rec.find")}
              </Link>
            </li>
            <li>
              <Link className="reception-tile-link" to={ROUTES.queue}>
                {t("queue.title")}
              </Link>
            </li>
            {billingCapability !== "denied" &&
              billingCapability !== "blocked_unverified" && (
                <li>
                  <Link className="reception-tile-link" to={ROUTES.checkout}>
                    {t("checkout.title")}
                  </Link>
                </li>
              )}
            {paymentEnabled && (
              <li>
                <WriteGate
                  capability="write.payment.receive"
                  reason={t("rec.checkoutPending")}
                >
                  <Button disabled disabledReason={t("rec.checkoutPending")}>
                    {t("v3.collect")}
                  </Button>
                </WriteGate>
              </li>
            )}
          </ul>
        </section>

        <section aria-labelledby="reception-kpis-title">
          <h2 id="reception-kpis-title">{t("rec.kpis")}</h2>
          <KpiGrid range="today" />
        </section>

        <section
          aria-labelledby="reception-today-title"
          className="reception-card"
        >
          <h2 id="reception-today-title">{t("rec.appointmentsTitle")}</h2>
          <TodayAppointments scope={scope} />
        </section>
      </div>
    </>
  );
}

export function ReceptionHomePage() {
  return (
    <RequireSession>
      <ReceptionMode />
    </RequireSession>
  );
}

function ReceptionMode() {
  const { session, authGeneration } = useSession();
  const { caps } = useCapabilities();
  return session?.mode === "live" ? (
    <>
      <LiveAppointments />
      {session.user.roles.includes("REC") &&
        getCapability(caps, "write.patient.create") === "enabled" && (
          <div className="shell-page-body">
            <PatientRegistration key={authGeneration} />
          </div>
        )}
    </>
  ) : (
    <ReceptionContent />
  );
}
