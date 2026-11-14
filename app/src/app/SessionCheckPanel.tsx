import { useTranslation } from "react-i18next";
import { StatePanel } from "../ui/StatePanel";
import { useSession } from "./SessionProvider";

/** Retry is explicitly GET-only. Never repeats login or other writes. */
export function SessionCheckPanel({ onRetry }: { onRetry?: () => void }) {
  const { status, refresh } = useSession();
  const { t } = useTranslation();
  if (status === "loading")
    return <StatePanel kind="loading" title={t("common.loading")} />;
  return (
    <StatePanel
      kind="error"
      title={t("session.checkFailed")}
      detail={t("session.checkFailedBody")}
      retryLabel={t("session.retryCheck")}
      onRetry={
        onRetry ??
        (() => {
          void refresh();
        })
      }
    />
  );
}
