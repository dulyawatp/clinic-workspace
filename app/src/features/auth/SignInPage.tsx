import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { consumeReturnTo, sanitizeReturnTo } from "../../app/return-to";
import { ROUTES } from "../../app/routes";
import { SessionCheckPanel } from "../../app/SessionCheckPanel";
import { useSession } from "../../app/SessionProvider";
import { type AppError, fromWire, toI18nKey } from "../../domain/errors";
import { postLogin } from "../../transport/auth";
import { Button } from "../../ui/Button";
import { LanguageToggle } from "../shell/LanguageToggle";
import { ShellBrand } from "../shell/SidebarNav";
import { WorkspaceChooser } from "./WorkspaceChooser";

export function SignInPage() {
  const { t } = useTranslation();
  const { refresh, status, workspaceHomePending } = useSession();
  const [checkingAfterLogin, setCheckingAfterLogin] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const submitting = useRef(false);
  const generation = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const usernameId = useId();
  const passwordId = useId();
  const errorId = useId();

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function checkAndContinue(current: number) {
    const result = await refresh();
    if (
      current !== generation.current ||
      !result.isCurrent() ||
      result.status === "superseded"
    )
      return;
    if (result.status === "anonymous" || result.status === "workspace") {
      setCheckingAfterLogin(false);
      return;
    }
    if (result.status !== "authenticated") return;
    const storedTarget = consumeReturnTo();
    navigate(
      sanitizeReturnTo(params.get("returnTo")) ?? storedTarget ?? ROUTES.home,
      { replace: true },
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || status !== "anonymous") return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const usr = String(values.get("usr") ?? "");
    const pwd = String(values.get("pwd") ?? "");
    const password = form.elements.namedItem("pwd");
    if (password instanceof HTMLInputElement) password.value = "";
    if (!usr.trim() || !pwd) {
      setError({ code: "VALIDATION", retryable: false });
      return;
    }

    submitting.current = true;
    const current = ++generation.current;
    setPending(true);
    setError(null);
    try {
      await postLogin(usr, pwd);
      if (current !== generation.current) return;
      setCheckingAfterLogin(true);
      await checkAndContinue(current);
    } catch (cause) {
      if (current !== generation.current) return;
      const code =
        typeof cause === "object" && cause !== null && "code" in cause
          ? fromWire(cause.code)
          : "UNKNOWN";
      setError({ code, retryable: false });
    } finally {
      if (current === generation.current) {
        submitting.current = false;
        setPending(false);
      }
    }
  }

  if (status === "workspace") return <WorkspaceChooser />;
  if (status === "authenticated" && workspaceHomePending)
    return <Navigate to={workspaceHomePending} replace />;

  return (
    <div className="signin-page">
      <a className="shell-skip" href="#signin-main">
        {t("action.skip")}
      </a>
      <aside className="signin-brand" aria-label={t("app.name")}>
        <ShellBrand />
        <div className="signin-statement">
          <h2>{t("auth.statementTitle")}</h2>
          <p>{t("auth.statementBody")}</p>
        </div>
        <p className="signin-language-note">{t("profile.localeDeviceOnly")}</p>
      </aside>
      <main className="signin-panel" id="signin-main" tabIndex={-1}>
        <div className="signin-panel-head">
          <LanguageToggle />
        </div>
        <div className="signin-form-wrap">
          {params.get("reason") === "expired" && status !== "authenticated" && (
            <div className="signin-banner signin-banner--expired" role="status">
              {t("page.signin.sessionExpired")}
            </div>
          )}
          <h1 id="signin-title">{t("page.signin.title")}</h1>
          <p id="signin-lead" className="signin-lead">
            {t(
              status === "authenticated"
                ? "session.alreadySignedIn"
                : "page.signin.lead",
            )}
          </p>
          {error && (
            <div
              ref={errorRef}
              id={errorId}
              role="alert"
              tabIndex={-1}
              className="signin-banner signin-banner--error"
            >
              <strong>{t("signin.errorTitle")}</strong>
              <p>{t(toI18nKey(error))}</p>
            </div>
          )}
          {status === "authenticated" ? (
            <Button
              className="signin-submit"
              aria-describedby="signin-lead"
              onClick={() => {
                setCheckingAfterLogin(true);
                void checkAndContinue(++generation.current);
              }}
            >
              {t("session.continueWorkspace")}
            </Button>
          ) : status === "error" || checkingAfterLogin ? (
            <SessionCheckPanel
              onRetry={() => {
                void checkAndContinue(++generation.current);
              }}
            />
          ) : (
            <form
              aria-labelledby="signin-title"
              aria-busy={pending}
              aria-describedby="signin-help signin-evaluation"
              onSubmit={(event) => void submit(event)}
            >
              <div className="signin-field">
                <label htmlFor={usernameId}>{t("field.username")}</label>
                <input
                  id={usernameId}
                  name="usr"
                  autoComplete="username"
                  required
                  disabled={pending || status === "loading"}
                  aria-describedby={error ? errorId : undefined}
                />
              </div>
              <div className="signin-field">
                <label htmlFor={passwordId}>{t("field.password")}</label>
                <input
                  id={passwordId}
                  name="pwd"
                  type="password"
                  autoComplete="current-password"
                  required
                  disabled={pending || status === "loading"}
                  aria-describedby={error ? errorId : undefined}
                />
              </div>
              <Button
                type="submit"
                className="signin-submit"
                disabled={pending || status === "loading"}
              >
                {pending ? t("common.loading") : t("page.signin.continue")}
              </Button>
            </form>
          )}
          <p id="signin-help" className="signin-help">
            {t("page.signin.help")}
          </p>
          <p id="signin-evaluation" className="signin-evaluation">
            {t("page.signin.demoNote")}
          </p>
        </div>
      </main>
    </div>
  );
}
