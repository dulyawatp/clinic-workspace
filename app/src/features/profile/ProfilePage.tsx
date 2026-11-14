import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { type AppError, fromWire, toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { LanguageToggle } from "../shell/LanguageToggle";

function ProfileContent() {
  const {
    session,
    logout: logoutSession,
    logoutFailure,
    clearLogoutFailure,
  } = useSession();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const query = useReadQuery<{ company?: unknown }>("profile");
  const value = query.data?.items[0]?.company;
  const company =
    !query.isError && typeof value === "string" && value.trim()
      ? value
      : t("common.unavailable");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Revocation unmounts this page while the logout POST is still in flight (the
  // route guard wraps the whole shell), so a failed logout after same-session
  // reconciliation is reported by the provider. Consume it once here so it
  // cannot leak into a replacement identity or a later view.
  useEffect(() => {
    if (logoutFailure === null) return;
    setError(logoutFailure);
    clearLogoutFailure();
  }, [logoutFailure, clearLogoutFailure]);

  async function logout() {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await logoutSession();
      if (!mounted.current) return;
      // Provider reconciles regardless of page lifetime. Only mounted callers
      // may navigate; protected route guards also handle confirmed anonymity.
      if (result.status === "anonymous" && result.isCurrent())
        navigate(ROUTES.login, { replace: true });
    } catch (cause) {
      if (!mounted.current) return;
      const code =
        typeof cause === "object" && cause !== null && "code" in cause
          ? fromWire(cause.code)
          : "UNKNOWN";
      setError({ code, retryable: false });
      busy.current = false;
      setPending(false);
    }
  }

  return (
    <>
      <PageHeader title={t("surface.APP-02")} />
      <div className="shell-page-body">
        <section
          className="profile-card"
          aria-labelledby="profile-account-title"
        >
          <h2 id="profile-account-title">{t("profile.account")}</h2>
          <dl className="profile-details">
            <dt>{t("profile.identity")}</dt>
            <dd data-testid="profile-name">
              {session?.user.displayName || t("common.unavailable")}
            </dd>
            <dt>{t("profile.role")}</dt>
            <dd>
              {session?.user.roles
                .map((role) => t(`role.${role.toLowerCase()}.short`))
                .join(" · ") || t("common.unavailable")}
            </dd>
            <dt>{t("field.company")}</dt>
            <dd>{company}</dd>
            <dt>{t("lang.label")}</dt>
            <dd>
              <LanguageToggle />
            </dd>
          </dl>
          {query.isPending && (
            <StatePanel kind="loading" title={t("common.loading")} />
          )}
          {query.isError && (
            <StatePanel
              kind="error"
              title={t("errors.UPSTREAM_UNAVAILABLE")}
              onRetry={() => {
                void query.refetch();
              }}
              retryLabel={t("action.retry")}
            />
          )}
          <p className="profile-locale-note">{t("profile.localeDeviceOnly")}</p>
          {error && <StatePanel kind="error" title={t(toI18nKey(error))} />}
          <div className="profile-actions" aria-busy={pending}>
            <Button
              variant="secondary"
              disabled={pending}
              onClick={() => void logout()}
            >
              {pending ? t("common.loading") : t("action.signOut")}
            </Button>
          </div>
        </section>
      </div>
    </>
  );
}

export function ProfilePage() {
  return (
    <RequireSession>
      <ProfileContent />
    </RequireSession>
  );
}
