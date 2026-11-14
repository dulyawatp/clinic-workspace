import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

/**
 * One record-derived count: the value comes from the server aggregate, and the
 * card carries both the date scope and the drilldown to the records it counts.
 */
export function KpiCard({
  testId,
  labelKey,
  value,
  scopeKey,
  href,
}: {
  testId: string;
  labelKey: string;
  value: number;
  scopeKey?: string;
  href: string;
}) {
  const { t } = useTranslation();

  return (
    <Link className="reception-kpi" to={href} data-testid={testId}>
      <span className="reception-kpi-label">{t(labelKey)}</span>
      <strong className="reception-kpi-value">{value}</strong>
      <span className="reception-kpi-meta">
        {scopeKey === undefined ? null : (
          <>
            <span>{t(scopeKey)}</span>
            {" · "}
          </>
        )}
        <span>{t("action.viewAll")} →</span>
      </span>
    </Link>
  );
}
