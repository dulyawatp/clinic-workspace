import { useTranslation } from "react-i18next";
import { translateClinicalText } from "../../i18n";
import {
  AllergyValue,
  type ClinicalIds,
  ClinicalRead,
  type ClinicalRow,
} from "./ClinicalRead";

export function OverviewSection({ patientId, encounterId }: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <ClinicalRead<ClinicalRow>
      resource="clinical-sections"
      patientId={patientId}
      encounterId={encounterId}
    >
      {(rows) => {
        const history = rows.find((row) => row.section === "history");
        if (!history)
          return <p className="clinical-empty">{t("clinical.noData")}</p>;
        return (
          <dl className="clinical-fields">
            <div>
              <dt>{t("clinical.complaint")}</dt>
              <dd>
                {history.chiefComplaint
                  ? translateClinicalText(history.chiefComplaint)
                  : t("clinical.sourceUnavailable")}
              </dd>
            </div>
            <div>
              <dt>{t("clinical.allergies")}</dt>
              <dd>
                <AllergyValue value={history.allergies} />
              </dd>
            </div>
          </dl>
        );
      }}
    </ClinicalRead>
  );
}
