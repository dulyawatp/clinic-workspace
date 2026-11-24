import {
  type ReactNode,
  useCallback,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { SessionCheckPanel } from "../../app/SessionCheckPanel";
import { useSession } from "../../app/SessionProvider";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "./CapabilityProvider";
import { DrawerNav } from "./DrawerNav";
import { navFor } from "./nav";
import { ShellBrand, SidebarNav } from "./SidebarNav";
import { Topbar } from "./Topbar";

const breakpoints = ["(min-width: 768px)", "(min-width: 1280px)"] as const;
function viewport() {
  if (window.matchMedia) {
    if (window.matchMedia(breakpoints[1]).matches) return "desktop";
    return window.matchMedia(breakpoints[0]).matches ? "tablet" : "mobile";
  }
  return window.innerWidth >= 1280
    ? "desktop"
    : window.innerWidth >= 768
      ? "tablet"
      : "mobile";
}
function subscribeViewport(notify: () => void) {
  const queries = window.matchMedia
    ? breakpoints.map((query) => window.matchMedia(query))
    : [];
  for (const query of queries) query.addEventListener("change", notify);
  window.addEventListener("resize", notify);
  return () => {
    for (const query of queries) query.removeEventListener("change", notify);
    window.removeEventListener("resize", notify);
  };
}

export function AppShell({ children }: { children: ReactNode }) {
  const { status, session } = useSession();
  const { caps } = useCapabilities();
  const { t } = useTranslation();
  const location = useLocation();
  const mode = useSyncExternalStore(
    subscribeViewport,
    viewport,
    () => "mobile",
  );
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  const drawerOpen = mode === "tablet" && drawerPath === location.key;
  const closeDrawer = useCallback(() => setDrawerPath(null), []);
  useEffect(() => {
    if (
      mode !== "tablet" ||
      drawerPath !== location.key ||
      status !== "authenticated"
    )
      setDrawerPath(null);
  }, [mode, location.key, drawerPath, status]);

  if (status === "loading")
    return (
      <div
        className="shell-loading"
        role="status"
        aria-busy="true"
        aria-label={t("common.loading")}
      >
        <span className="shell-sr-only">{t("common.loading")}</span>
        <div className="shell-skeleton-line" />
        <div className="shell-skeleton-panel" />
      </div>
    );
  if (status === "error")
    return (
      <main className="shell-guest">
        <SessionCheckPanel />
      </main>
    );
  if (status !== "authenticated" || !session)
    return (
      <main className="shell-guest">
        <ShellBrand />
        <StatePanel
          kind="empty"
          title={t("shell.authRequired")}
          detail={t("shell.authRequiredBody")}
        />
        <Link className="ui-button ui-button--primary" to={ROUTES.login}>
          {t("page.signin.title")}
        </Link>
      </main>
    );

  const items = navFor(session.user.roles, caps, session.mode);
  return (
    <div className={`clinic-shell clinic-shell--${mode}`}>
      <a className="shell-skip" href="#main">
        {t("action.skip")}
      </a>
      {mode === "desktop" && (
        <aside className="shell-sidebar">
          <ShellBrand />
          <SidebarNav items={items} />
          <div className="shell-sidebar-foot">{t("app.tagline")}</div>
        </aside>
      )}
      <div className="shell-body">
        <Topbar
          onOpenMenu={
            mode === "tablet" ? () => setDrawerPath(location.key) : undefined
          }
          drawerOpen={drawerOpen}
        />
        <main id="main" tabIndex={-1} className="shell-main">
          {children}
        </main>
      </div>
      {mode === "tablet" && drawerOpen && (
        <DrawerNav items={items} onClose={closeDrawer} />
      )}
      {mode === "mobile" && <SidebarNav variant="bottom" items={items} />}
    </div>
  );
}
