import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { type AppError, fromWire } from "../../domain/errors";
import { StatePanel } from "../../ui/StatePanel";

export function WorkItemsError({
  error,
  onRetry,
}: {
  error: AppError;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { refresh } = useSession();
  const code = fromWire(error?.code);
  const title = t(
    code === "UNSUPPORTED" ? "notif.unsupported" : `errors.${code}`,
  );

  if (code === "AUTH_EXPIRED") {
    return (
      <StatePanel
        kind="error"
        title={title}
        onRetry={() => {
          void refresh();
        }}
        retryLabel={t("action.retry")}
      />
    );
  }

  return error?.retryable === true && code !== "UNSUPPORTED" ? (
    <StatePanel
      kind="error"
      title={title}
      onRetry={onRetry}
      retryLabel={t("action.retry")}
    />
  ) : (
    <StatePanel kind="error" title={title} />
  );
}
