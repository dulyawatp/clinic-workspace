import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../ui/Button";
import { LanguageToggle } from "../shell/LanguageToggle";
import { ShellBrand } from "../shell/SidebarNav";

/** Deliberately outside the protected shell: choices alone authorize no UI. */
export function WorkspaceChooser() {
  const { t } = useTranslation();
  const { session, chooseWorkspace, logout, previousWorkspace, workspaceSelectionFailed } = useSession();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);

  return (
    <div className="signin-page">
      <aside className="signin-brand" aria-label={t("app.name")}>
        <ShellBrand />
        <div className="signin-statement">
          <h2>{t("auth.statementTitle")}</h2>
          <p>{t("auth.statementBody")}</p>
        </div>
      </aside>
      <main className="signin-panel" aria-labelledby="workspace-chooser-title">
        <div className="signin-panel-head"><LanguageToggle /></div>
        <div className="signin-form-wrap">
          <h1 id="workspace-chooser-title" ref={heading} tabIndex={-1}>
            {t("workspace.choose")}
          </h1>
          <p className="signin-lead" id="workspace-chooser-lead">{t("workspace.chooseLead")}</p>
          {workspaceSelectionFailed && (
            <div className="signin-banner signin-banner--error" role="alert">
              {t("workspace.chooseFailed")}
            </div>
          )}
          <ul className="workspace-choices" aria-describedby="workspace-chooser-lead">
            {session?.eligibleWorkspaces?.map((role) => (
              <li key={role}>
                <Button variant="secondary" className="workspace-choice" onClick={() => void chooseWorkspace(role)}>
                  <strong>{t(`role.${role.toLowerCase()}.short`)}</strong>
                  {role === previousWorkspace && <span>{t("workspace.current")}</span>}
                  <span className="workspace-choice-arrow" aria-hidden="true">→</span>
                </Button>
              </li>
            ))}
          </ul>
          <Button variant="secondary" onClick={() => void logout()}>
            {t("action.signOut")}
          </Button>
        </div>
      </main>
    </div>
  );
}
