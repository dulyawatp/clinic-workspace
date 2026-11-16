import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import type { NavItem } from "./nav";

const iconPaths: Record<string, string> = {
  home: "M3 10 12 3l9 7v11h-6v-7H9v7H3Z",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  profile:
    "M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2M16 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  system: "M4 3h16v18H4ZM8 8h8M8 12h8M8 16h5",
  patients:
    "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6M3 20a6 6 0 0 1 12 0M17 8h4M17 12h4",
  reception:
    "M4 20V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14M8 8h8M8 12h8M8 16h4M2 20h20",
  appointments: "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16",
  queue: "M4 6h2M10 6h10M4 12h2M10 12h10M4 18h2M10 18h10",
  checkout: "M5 3h14v18l-3-2-4 2-4-2-3 2zM8 8h8M8 12h8",
  pharmacy: "M9 3h6v6h6v6h-6v6H9v-6H3V9h6z",
  clipboard: "M9 4h6v3H9zM7 6h10v15H7zM10 12h4M10 16h4",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3",
  box: "M3 8l9-5 9 5-9 5-9-5M3 8v8l9 5 9-5V8",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  chart: "M4 20V11M10 20V4M16 20v-6M2 20h20",
  truck: "M3 16V6h11v10H3M14 10h4l3 3v3M6 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4M17 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4",
  transfer: "M4 7h13l-3-3M20 17H7l3 3",
  receipt: "M5 3h14v18l-3-2-4 2-4-2-3 2zM8 8h8M8 12h8",
  cash: "M2 6h20v12H2zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6",
  shield: "M12 3l8 3v6c0 5-3.4 8.2-8 9-4.6-.8-8-4-8-9V6zM9 12l2 2 4-4",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6M4 12h2M18 12h2M12 4v2M12 18v2",
  activity: "M3 12h4l2.5-6 4 12 2.5-6h5",
  menu: "M3 6h18M3 12h18M3 18h18",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4",
  close: "m5 5 14 14M19 5 5 19",
};

export function NavIcon({ name }: { name: string }) {
  return (
    <svg
      className="shell-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={iconPaths[name] ?? iconPaths.system} />
    </svg>
  );
}

export function ShellBrand() {
  const { t } = useTranslation();
  return (
    <div className="shell-brand">
      <span className="shell-brand-mark" aria-hidden="true">
        +
      </span>
      <div>
        <strong>{t("app.name")}</strong>
        <span>{t("app.tagline")}</span>
      </div>
    </div>
  );
}

export function SidebarNav({
  items,
  variant = "sidebar",
  onNavigate,
}: {
  items: readonly NavItem[];
  variant?: "sidebar" | "drawer" | "bottom";
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const link = (item: NavItem) => (
    <NavLink
      className="shell-nav-link"
      to={ROUTES[item.routeKey]}
      end={item.exact === true}
      onClick={onNavigate}
    >
      <NavIcon name={item.icon} />
      <span>{t(item.labelKey)}</span>
    </NavLink>
  );

  return (
    <nav
      className={`shell-nav shell-nav--${variant}`}
      aria-label={t("nav.primary")}
    >
      {variant !== "bottom" && (
        <p className="shell-nav-label">{t("nav.group.shared")}</p>
      )}
      <ul className="shell-nav-primary">
        {(variant === "drawer" ? items : items.slice(0, 6)).map((item) => (
          <li key={item.id}>{link(item)}</li>
        ))}
        {variant !== "drawer" && items.length > 6 && (
          <li className="shell-more">
            <details>
              <summary>{t("nav.more")}</summary>
              <ul aria-label={t("nav.more")}>
                {items.slice(6).map((item) => (
                  <li key={item.id}>{link(item)}</li>
                ))}
              </ul>
            </details>
          </li>
        )}
      </ul>
    </nav>
  );
}
