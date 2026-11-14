import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { useReadQuery } from "../../transport/read/query";
import { LanguageToggle } from "./LanguageToggle";
import { NotificationsPanel } from "./NotificationsPanel";
import { NavIcon } from "./SidebarNav";

/** Mounted only inside authenticated Topbar; never races the session read. */
function Company() {
  const { t } = useTranslation();
  const query = useReadQuery<{ company?: unknown }>("profile");
  const value = query.data?.items[0]?.company;
  const company =
    !query.isError && typeof value === "string" && value.trim()
      ? value
      : t("common.unavailable");
  return (
    <div className="shell-company">
      <span>{t("field.company")}</span>
      <strong data-testid="shell-company">{company}</strong>
    </div>
  );
}

export function Topbar({
  onOpenMenu,
  drawerOpen = false,
}: {
  onOpenMenu?: () => void;
  drawerOpen?: boolean;
}) {
  const { status, session } = useSession();
  const { t } = useTranslation();
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const notificationId = useId();
  const notificationRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const topbarRef = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    if (status !== "authenticated") return;
    const topbar = topbarRef.current;
    const shellBody = topbar?.parentElement;
    if (!topbar || !shellBody) return;
    const measure = () => {
      shellBody.style.setProperty(
        "--shell-topbar-height",
        `${topbar.getBoundingClientRect().height}px`,
      );
    };
    measure();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    observer?.observe(topbar);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      shellBody.style.removeProperty("--shell-topbar-height");
    };
  }, [status]);

  useEffect(() => {
    if (status !== "authenticated") setNotificationsOpen(false);
  }, [status]);

  useEffect(() => {
    if (!notificationsOpen) return;
    popoverRef.current?.focus();
    const dismiss = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !notificationRef.current?.contains(event.target)
      )
        setNotificationsOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [notificationsOpen]);

  if (status !== "authenticated" || !session) return null;
  const roles = session.user.roles
    .map((role) => t(`role.${role.toLowerCase()}.short`))
    .join(" · ");

  return (
    <header ref={topbarRef} className="shell-topbar">
      {onOpenMenu && (
        <button
          type="button"
          className="shell-icon-control"
          aria-label={t("nav.openMenu")}
          aria-expanded={drawerOpen}
          aria-haspopup="dialog"
          aria-controls={drawerOpen ? "shell-drawer" : undefined}
          onClick={onOpenMenu}
        >
          <NavIcon name="menu" />
        </button>
      )}
      <div className="shell-context">
        <span className="shell-role">
          {t("shell.roleContext")}{" "}
          <strong>{roles || t("common.unavailable")}</strong>
        </span>
        <Company />
      </div>
      <div className="shell-topbar-actions">
        <Link
          to={ROUTES.search}
          className="shell-icon-control"
          aria-label={t("nav.search")}
        >
          <NavIcon name="search" />
        </Link>
        <LanguageToggle />
        <div className="shell-notifications" ref={notificationRef}>
          <button
            ref={triggerRef}
            type="button"
            className="shell-icon-control"
            aria-label={t("shell.notifications")}
            aria-expanded={notificationsOpen}
            aria-haspopup="dialog"
            aria-controls={notificationsOpen ? notificationId : undefined}
            onClick={() => setNotificationsOpen((open) => !open)}
            onBlur={(event) => {
              if (!notificationRef.current?.contains(event.relatedTarget))
                setNotificationsOpen(false);
            }}
          >
            <NavIcon name="bell" />
          </button>
          {notificationsOpen && (
            <div
              ref={popoverRef}
              id={notificationId}
              className="shell-popover"
              role="dialog"
              aria-label={t("shell.notifications")}
              tabIndex={-1}
              onBlur={(event) => {
                if (!notificationRef.current?.contains(event.relatedTarget))
                  setNotificationsOpen(false);
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  setNotificationsOpen(false);
                  triggerRef.current?.focus();
                }
              }}
            >
              <div className="work-items-heading">
                <strong>{t("shell.notifications")}</strong>
                <button
                  type="button"
                  className="shell-icon-control"
                  aria-label={t("action.close")}
                  onClick={() => {
                    setNotificationsOpen(false);
                    triggerRef.current?.focus();
                  }}
                >
                  ×
                </button>
              </div>
              <NotificationsPanel
                onNavigate={() => setNotificationsOpen(false)}
              />
            </div>
          )}
        </div>
        <Link
          to={ROUTES.profile}
          className="shell-icon-control"
          aria-label={t("shell.profileMenu")}
        >
          <NavIcon name="profile" />
        </Link>
      </div>
    </header>
  );
}
