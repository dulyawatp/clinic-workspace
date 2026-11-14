import { useTranslation } from "react-i18next";

export function StatusMappingNote({
  raw,
  source,
}: {
  raw: string;
  source: string;
}) {
  const { t } = useTranslation();
  return (
    <small>
      {t("status.unmapped")} · {t("queue.source")}: {source} · {raw}
    </small>
  );
}
