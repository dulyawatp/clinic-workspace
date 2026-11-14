import { useTranslation } from "react-i18next";
import type { ROUTES } from "../../app/routes";
import { StatePanel } from "../../ui/StatePanel";

const RESOURCE_LABELS: Partial<Record<keyof typeof ROUTES, string>> = {
  home: "nav.today",
  search: "nav.search",
  profile: "nav.profile",
  system: "nav.system",
  login: "page.signin.title",
  patients: "nav.patients",
};

/** Resource is a canonical route key, never a record path, name or identifier. */
export function PermissionDenied({
  resource = "home",
}: {
  resource?: keyof typeof ROUTES;
}) {
  const { t } = useTranslation();
  return (
    <div className="shell-page-body">
      <StatePanel
        kind="permission-denied"
        title={t("state.denied.title")}
        detail={t("state.denied.body", {
          resource: t(RESOURCE_LABELS[resource] ?? "nav.today"),
        })}
      />
      <p>{t("state.denied.note")}</p>
    </div>
  );
}
