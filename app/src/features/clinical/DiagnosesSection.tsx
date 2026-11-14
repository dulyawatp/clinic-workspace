import { useTranslation } from "react-i18next";
import { translateClinicalText } from "../../i18n";
import type { ClinicalIds } from "./ClinicalRead";
import { PagedClinicalRead } from "./PagedClinicalRead";

interface Diagnosis extends ClinicalIds {
  id: string;
  code: string;
  description: string;
}
export function DiagnosesSection(ids: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <PagedClinicalRead<Diagnosis>
      key={JSON.stringify([ids.patientId, ids.encounterId])}
      resource="clinical-diagnoses"
      {...ids}
    >
      {(rows, total) =>
        rows.length ? (
          <ul className="clinical-order-list">
            {rows.map((row) => (
              <li key={row.id}>
                <strong>{row.code}</strong>
                <span>{translateClinicalText(row.description)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="clinical-empty">
            {t(total === 0 ? "clinical.noDiagnoses" : "clinical.noDataPage")}
          </p>
        )
      }
    </PagedClinicalRead>
  );
}
