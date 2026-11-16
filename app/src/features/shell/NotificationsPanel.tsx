import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES, type StaticRouteKey } from "../../app/routes";
import { SessionCheckPanel } from "../../app/SessionCheckPanel";
import { useSession } from "../../app/SessionProvider";
import { getCapability, isCapability } from "../../domain/capability";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "./CapabilityProvider";
import { navFor } from "./nav";
import { useWorkItems } from "./useWorkItems";

export function NotificationsPanel({
  onNavigate,
}: {
  onNavigate?: () => void;
}) {
  const { status, session } = useSession();
  const { caps } = useCapabilities();
  const { t, i18n } = useTranslation();
  const query = useWorkItems();
  const destinations = new Set(
    navFor(session?.user.roles ?? [], caps).map((item) => item.routeKey),
  );
  const visible = query.isError
    ? []
    : (query.data?.items ?? []).filter((item) => {
        if (
          !item ||
          typeof item.id !== "string" ||
          typeof item.labelKey !== "string" ||
          !/^(nav|status)\./.test(item.labelKey) ||
          !i18n.exists(item.labelKey) ||
          !Number.isSafeInteger(item.count) ||
          item.count < 0 ||
          typeof item.routeKey !== "string" ||
          !Object.hasOwn(ROUTES, item.routeKey) ||
          !destinations.has(item.routeKey as StaticRouteKey)
        )
          return false;
        if (item.requiredCapability !== undefined) {
          if (!isCapability(item.requiredCapability)) return false;
          const state = getCapability(caps, item.requiredCapability);
          if (state === "denied" || state === "blocked_unverified")
            return false;
        }
        return true;
      });

  return (
    <section data-testid="work-items" aria-label={t("shell.notifications")}>
      {status === "loading" || status === "error" ? (
        <SessionCheckPanel />
      ) : status !== "authenticated" ? null : query.isError ? (
        <StatePanel
          kind="error"
          title={t("errors.UPSTREAM_UNAVAILABLE")}
          onRetry={() => {
            void query.refetch();
          }}
          retryLabel={t("action.retry")}
        />
      ) : query.isPending ? (
        <StatePanel kind="loading" title={t("common.loading")} />
      ) : visible.length === 0 ? (
        <p className="work-items-empty">{t("notif.empty")}</p>
      ) : (
        <ul className="work-items-list">
          {visible.map((item) => (
            <li key={item.id}>
              <Link
                to={ROUTES[item.routeKey as StaticRouteKey]}
                onClick={onNavigate}
              >
                <span>{t(item.labelKey)}</span>
                <strong className="work-item-count">{item.count}</strong>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
