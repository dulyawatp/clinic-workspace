import { Fragment, type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router-dom";
import { WorkspaceChooser } from "../features/auth/WorkspaceChooser";
import { StatePanel } from "../ui/StatePanel";
import { consumeReturnTo, rememberReturnTo } from "./return-to";
import { ROUTES } from "./routes";
import { SessionCheckPanel } from "./SessionCheckPanel";
import { useSession } from "./SessionProvider";

/** UI/session presentation only. Proxy must authorize every backend request. */
export function RequireSession({ children }: { children: ReactNode }) {
  const { status, authGeneration, workspaceHomePending, clearWorkspaceHomePending } = useSession();
  const location = useLocation();
  const { t } = useTranslation();
  const atWorkspaceHome = location.pathname === workspaceHomePending && !location.search && !location.hash;

  useEffect(() => {
    if (status === "anonymous")
      rememberReturnTo(location.pathname + location.search);
  }, [status, location.pathname, location.search]);

  useEffect(() => {
    if (atWorkspaceHome && status === "authenticated") {
      consumeReturnTo();
      clearWorkspaceHomePending();
    }
  }, [atWorkspaceHome, status, clearWorkspaceHomePending]);

  if (status === "loading")
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (status === "error") return <SessionCheckPanel />;
  if (status === "anonymous")
    return <Navigate to={`${ROUTES.login}?reason=expired`} replace />;
  if (status === "workspace") return <WorkspaceChooser />;
  if (workspaceHomePending && !atWorkspaceHome)
    return <Navigate to={workspaceHomePending} replace />;
  return <Fragment key={authGeneration}>{children}</Fragment>;
}
