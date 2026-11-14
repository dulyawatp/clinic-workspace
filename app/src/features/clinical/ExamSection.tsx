import { useTranslation } from "react-i18next";
import { translateClinicalText } from "../../i18n";
import {
  type ClinicalIds,
  ClinicalRead,
  type ClinicalRow,
} from "./ClinicalRead";
import { VitalsRead } from "./VitalsRead";

export function ExamSection({ patientId, encounterId }: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <div className="clinical-exam">
      <section>
        <h3>{t("clinical.findings")}</h3>
        <ClinicalRead<ClinicalRow>
          resource="clinical-sections"
          patientId={patientId}
          encounterId={encounterId}
        >
          {(rows) => {
            const exam = rows.find((row) => row.section === "exam");
            return exam ? (
              <dl className="clinical-fields">
                <div>
                  <dt>{t("clinical.findings")}</dt>
                  <dd>
                    {exam.findings
                      ? translateClinicalText(exam.findings)
                      : t("clinical.sourceUnavailable")}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="clinical-empty">{t("clinical.noData")}</p>
            );
          }}
        </ClinicalRead>
      </section>
      <section>
        <h3>{t("clinical.vitals")}</h3>
        <VitalsRead
          key={JSON.stringify([patientId, encounterId])}
          patientId={patientId}
          encounterId={encounterId}
        />
      </section>
    </div>
  );
}
