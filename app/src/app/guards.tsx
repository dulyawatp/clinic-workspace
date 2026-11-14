import { type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router-dom";
import { StatePanel } from "../ui/StatePanel";
import { rememberReturnTo } from "./return-to";
import { ROUTES } from "./routes";
import { SessionCheckPanel } from "./SessionCheckPanel";
import { useSession } from "./SessionProvider";

/** UI/session presentation only. Proxy must authorize every backend request. */
export function RequireSession({ children }: { children: ReactNode }) {
  const { status } = useSession();
  const location = useLocation();
  const { t } = useTranslation();

  useEffect(() => {
    if (status === "anonymous")
      rememberReturnTo(location.pathname + location.search);
  }, [status, location.pathname, location.search]);

  if (status === "loading")
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (status === "error") return <SessionCheckPanel />;
  if (status === "anonymous")
    return <Navigate to={`${ROUTES.login}?reason=expired`} replace />;
  return children;
}
