import { useTranslation } from "react-i18next";
import type { LatestVitals } from "../../domain/patient";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";

interface VitalReading {
  key: keyof LatestVitals;
  labelKey: string;
  unit: string;
}

const READINGS: readonly VitalReading[] = [
  { key: "temp", labelKey: "profile.vital.temp", unit: "°C" },
  { key: "pulse", labelKey: "profile.vital.pulse", unit: "bpm" },
  { key: "systolic", labelKey: "profile.vital.systolic", unit: "mmHg" },
  { key: "diastolic", labelKey: "profile.vital.diastolic", unit: "mmHg" },
  { key: "respiratory", labelKey: "profile.vital.respiratory", unit: "/min" },
  { key: "height", labelKey: "profile.vital.height", unit: "cm" },
  { key: "weight", labelKey: "profile.vital.weight", unit: "kg" },
];

/**
 * Latest measurement batch only. Measurement time, source and actor are always
 * shown with the values, and an absent measurement is labelled "not measured"
 * — never printed as a zero.
 */
export function VitalsSummary({ latest }: { latest?: LatestVitals }) {
  const { t } = useTranslation();
  const { locale } = useLocale();

  return (
    <section
      className="patients-card patients-vitals"
      aria-labelledby="patients-vitals-title"
    >
      <h2 id="patients-vitals-title">{t("profile.latestVitals")}</h2>
      {latest === undefined ? (
        <p className="patients-vitals-empty">{t("profile.noMeasurements")}</p>
      ) : (
        <>
          <p className="patients-vitals-meta">
            <span>{t("profile.measuredAt")}</span>{" "}
            <time dateTime={latest.measuredAt}>
              {formatDateTime(latest.measuredAt, locale)}
            </time>
            {" · "}
            <span>{t("profile.source")}</span>{" "}
            <span className="mono">{latest.source}</span>
            {" · "}
            <span>{t("profile.recordedBy")}</span>{" "}
            <span>{latest.recordedBy}</span>
          </p>
          <dl className="patients-vitals-values">
            {READINGS.map((reading) => {
              const value = latest[reading.key];
              return (
                <div key={reading.key}>
                  <dt>{t(reading.labelKey)}</dt>
                  <dd>
                    {typeof value === "number"
                      ? `${value} ${reading.unit}`
                      : t("profile.notMeasured")}
                  </dd>
                </div>
              );
            })}
          </dl>
        </>
      )}
    </section>
  );
}
