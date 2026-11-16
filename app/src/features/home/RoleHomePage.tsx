import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES, type StaticRouteKey } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Card, PageHeader, UnavailablePanel } from "../../design/components";
import {
  roleHomeSurface,
  surfaceFor,
  surfacePath,
} from "../../design/surfaces";
import { useCapabilities } from "../shell/CapabilityProvider";
import { navFor } from "../shell/nav";
import { HomeWorkItems } from "./HomeWorkItems";
import "./home.css";

/**
 * APP-02 role-home dispatch. The first signed-in role opens its designed home;
 * shortcuts come from the same role-gated navigation model, and the work-item
 * list reads the role-scoped fixture with browser-local read/dismiss state.
 */
export function RoleHomePage() {
  const { t } = useTranslation();
  const { session } = useSession();
  const { caps } = useCapabilities();
  const roles = session?.user.roles ?? [];
  const home = roleHomeSurface(roles);
  const spec = home ? surfaceFor(home) : undefined;
  const shortcuts = spec
    ? navFor(roles, caps)
        .filter(
          (item) =>
            item.routeKey !== "home" &&
            item.routeKey !== "profile" &&
            item.routeKey !== spec.routeKey,
        )
        .slice(0, 6)
    : [];
  const roleNames =
    roles
      .map((role) => t(`role.${role.toLowerCase()}.short`))
      .join(" · ") || t("common.unavailable");

  return (
    <div data-testid="role-home" data-surface="APP-02">
      <PageHeader
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("roleHome.title")}
        subtitle={t("roleHome.sub")}
      />
      <div className="pc-page__body role-home">
        {spec ? (
          <div className="role-home__grid">
            <Card
              title={t(spec.titleKey)}
              hint={`${spec.id} · ${t("roleHome.roleHomeHint")}`}
              actions={
                <Link
                  className="pc-button pc-button--primary"
                  to={surfacePath(spec.id)}
                >
                  {t("action.open")}
                </Link>
              }
            >
              <dl className="role-home__facts">
                <div>
                  <dt>{t("roleHome.signedInAs")}</dt>
                  <dd>{session?.user.displayName || t("common.unavailable")}</dd>
                </div>
                <div>
                  <dt>{t("roleHome.roles")}</dt>
                  <dd>{roleNames}</dd>
                </div>
              </dl>
            </Card>

            <Card
              title={t("roleHome.shortcuts")}
              hint={t("roleHome.shortcutsHint")}
            >
              {shortcuts.length === 0 ? (
                <p className="role-home__empty">{t("roleHome.noShortcuts")}</p>
              ) : (
                <ul className="role-home__shortcuts">
                  {shortcuts.map((item) => (
                    <li key={item.id}>
                      <Link
                        className="role-home__shortcut"
                        to={ROUTES[item.routeKey as StaticRouteKey]}
                      >
                        <span>{t(item.labelKey)}</span>
                        <span aria-hidden="true">→</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        ) : (
          <UnavailablePanel
            title={t("shell.homePending")}
            detail={t("shell.homePendingBody")}
          />
        )}

        <HomeWorkItems />

        <Card title={t("roleHome.profileTitle")}>
          <p>{t("roleHome.profileBody")}</p>
          <Link className="pc-button pc-button--secondary" to={ROUTES.profile}>
            {t("roleHome.openProfile")}
          </Link>
        </Card>
      </div>
    </div>
  );
}
