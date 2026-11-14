import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { PatientSummary } from "./usePatientSearch";

/** Summary fields only. No invented patient-route links or clinical columns. */
export function SearchResultList({
  items,
  focusOnMount = false,
}: {
  items: readonly PatientSummary[];
  focusOnMount?: boolean;
}) {
  const { t } = useTranslation();
  const resultsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focusOnMount) resultsRef.current?.focus();
  }, [focusOnMount]);
  return (
    <section
      ref={resultsRef}
      className="search-results-scroll"
      aria-label={t("search.results")}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need to focus this overflow region to scroll the table.
      tabIndex={0}
    >
      <table className="search-results-table">
        <caption className="shell-sr-only">{t("search.results")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("search.patientId")}</th>
            <th scope="col">{t("search.patientName")}</th>
            <th scope="col">{t("search.phone")}</th>
            <th scope="col">{t("search.lastVisit")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((patient) => (
            <tr key={patient.id}>
              <td className="search-patient-number">{patient.hn}</td>
              <td className="search-patient-name">{patient.fullName}</td>
              <td>{patient.phone}</td>
              <td>
                {patient.lastVisit ? (
                  <time dateTime={patient.lastVisit}>{patient.lastVisit}</time>
                ) : (
                  t("common.unavailable")
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
