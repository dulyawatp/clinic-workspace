import { useTranslation } from "react-i18next";
import type { TimelineEntry } from "../../domain/patient";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { StatePanel } from "../../ui/StatePanel";

/**
 * Read-only history for one patient. The caller has already permission-filtered
 * the kinds: a kind this reader may not see is never passed in, so a hidden
 * entry cannot be resurrected here from an id, a count or a label.
 */
export function Timeline({ items }: { items: readonly TimelineEntry[] }) {
  const { t } = useTranslation();
  const { locale } = useLocale();

  if (items.length === 0)
    return <StatePanel kind="empty" title={t("profile.historyEmpty")} />;

  return (
    <ol className="patients-timeline">
      {items.map((item) => (
        <li key={item.id} className="patients-timeline-item">
          <time className="patients-timeline-time" dateTime={item.at}>
            {formatDateTime(item.at, locale)}
          </time>
          <span className="patients-timeline-kind">
            {t(`profile.kind.${item.kind}`)}
          </span>
          <span className="patients-id">{item.reference}</span>
        </li>
      ))}
    </ol>
  );
}
