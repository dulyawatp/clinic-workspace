import { useTranslation } from "react-i18next";
import type { Vital } from "./ClinicalRead";

export function VitalsTable({ vitals }: { vitals: Vital[] }) {
  const { t } = useTranslation();
  if (!vitals.length)
    return <p className="clinical-empty">{t("clinical.noData")}</p>;
  return (
    <ul className="clinical-vitals">
      {vitals.map((vital) => (
        <li key={vital.id}>
          <h4>
            {vital.kind === "weight"
              ? t("clinical.weight")
              : vital.kind === "temperature"
                ? t("clinical.temperature")
                : vital.kind}
          </h4>
          <dl>
            <div>
              <dt>{t("clinical.value")}</dt>
              <dd className="clinical-vital-value">
                {vital.value === null
                  ? t("clinical.notRecorded")
                  : `${vital.value} ${vital.unit}`}
              </dd>
            </div>
            <div>
              <dt>{t("clinical.measuredAt")}</dt>
              <dd>
                <time dateTime={vital.measuredAt}>{vital.measuredAt}</time>
              </dd>
            </div>
            <div>
              <dt>{t("clinical.source")}</dt>
              <dd>{vital.source}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}
