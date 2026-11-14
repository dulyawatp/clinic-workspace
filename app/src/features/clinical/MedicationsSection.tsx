import { useTranslation } from "react-i18next";
import { translateClinicalText } from "../../i18n";
import type { ClinicalIds } from "./ClinicalRead";
import { PagedClinicalRead } from "./PagedClinicalRead";

interface Medication extends ClinicalIds {
  id: string;
  name: string;
  state: string;
}
export function MedicationsSection(ids: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <PagedClinicalRead<Medication>
      key={JSON.stringify([ids.patientId, ids.encounterId])}
      resource="clinical-medications"
      {...ids}
    >
      {(rows, total) =>
        rows.length ? (
          <ul className="clinical-order-list">
            {rows.map((row) => (
              <li key={row.id}>
                <strong>{translateClinicalText(row.name)}</strong>
                <span>
                  {t("clinical.reportedState")}:{" "}
                  {row.state === "ordered" ? (
                    t("clinical.ordered")
                  ) : (
                    <>
                      {row.state} · {t("clinical.unrecognized")}
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="clinical-empty">
            {t(total === 0 ? "clinical.noMedications" : "clinical.noDataPage")}
          </p>
        )
      }
    </PagedClinicalRead>
  );
}
