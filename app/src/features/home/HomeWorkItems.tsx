import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES, type StaticRouteKey } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, Card, SampleBanner } from "../../design/components";
import {
  getCapability,
  isCapability,
} from "../../domain/capability";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { navFor } from "../shell/nav";
import { useWorkItems } from "../shell/useWorkItems";
import { useWorkItemMarks } from "./useWorkItemMarks";

/**
 * APP-02 work items. Rows come from the role-scoped read fixture and carry a
 * Demo/Sample label; read and dismissed flags are browser-local per identity.
 */
export function HomeWorkItems() {
  const { t, i18n } = useTranslation();
  const { session } = useSession();
  const { caps } = useCapabilities();
  const query = useWorkItems();
  const { marks, markRead, dismiss } = useWorkItemMarks(session?.user.id);
  const destinations = new Set(
    navFor(session?.user.roles ?? [], caps).map((item) => item.routeKey),
  );

  const visible = (query.data?.items ?? []).filter((item) => {
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
      !destinations.has(item.routeKey as StaticRouteKey) ||
      marks[item.id]?.dismissed
    )
      return false;
    if (item.requiredCapability !== undefined) {
      if (!isCapability(item.requiredCapability)) return false;
      const state = getCapability(caps, item.requiredCapability);
      if (state === "denied" || state === "blocked_unverified") return false;
    }
    return true;
  });
  const unread = visible.filter((item) => !marks[item.id]?.read).length;

  return (
    <div data-testid="home-work-items">
      <Card
        title={t("shell.notifications")}
        hint={t("roleHome.workItemsHint")}
        actions={
          <Badge tone={unread > 0 ? "demo" : "neutral"}>
            {t("roleHome.unread", { count: unread })}
          </Badge>
        }
      >
        <SampleBanner label={t("app.demoTag")} title={t("notif.demoNote")} />
        {query.isError ? (
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
          <p className="role-home__empty">{t("notif.empty")}</p>
        ) : (
          <ul className="role-home__workitems">
            {visible.map((item) => {
              const read = marks[item.id]?.read === true;
              return (
                <li
                  key={item.id}
                  className="role-home__workitem"
                  data-read={read ? "true" : "false"}
                >
                  <Link
                    className="role-home__workitem-link"
                    to={ROUTES[item.routeKey as StaticRouteKey]}
                    onClick={() => markRead(item.id)}
                  >
                    {!read && (
                      <span className="role-home__unread-dot" aria-hidden="true">
                        ●
                      </span>
                    )}
                    <span>{t(item.labelKey)}</span>
                    <strong>{item.count}</strong>
                  </Link>
                  <button
                    type="button"
                    className="role-home__dismiss"
                    aria-label={`${t("action.dismiss")} · ${t(item.labelKey)}`}
                    onClick={() => dismiss(item.id)}
                  >
                    ×
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
