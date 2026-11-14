import { useTranslation } from "react-i18next";

export function DraftStatusBar({
  state,
  savedAt,
  savedBy,
}: {
  state?: string;
  savedAt?: string | null;
  savedBy?: string | null;
}) {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    none: "clinical.noteNone",
    draft: "clinical.noteDraft",
    signed: "clinical.noteSigned",
    amended: "clinical.noteAmended",
  };
  const label =
    state && Object.hasOwn(labels, state) ? labels[state] : undefined;
  return (
    <div className="clinical-status">
      <p>
        <strong>{t("clinical.reportedState")}: </strong>
        {state ? (
          label ? (
            t(label)
          ) : (
            <>
              {state} · {t("clinical.unrecognized")}
            </>
          )
        ) : (
          t("clinical.sourceUnavailable")
        )}
      </p>
      {savedAt && (
        <p>
          {t("clinical.lastSaved")}: <time dateTime={savedAt}>{savedAt}</time>
        </p>
      )}
      {savedBy && (
        <p>
          {t("clinical.savedBy")}: {savedBy}
        </p>
      )}
    </div>
  );
}
