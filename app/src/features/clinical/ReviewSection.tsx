import { useTranslation } from "react-i18next";
import type { VisitSection } from "../../domain/clinical";
import type { ClinicalIds } from "./ClinicalRead";
import { PagedClinicalRead } from "./PagedClinicalRead";
import { SECTION_ORDER, sectionLabelKey } from "./sections";

interface ReviewRow extends ClinicalIds {
  section: string;
  missing?: string[];
}
export function ReviewSection(ids: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <PagedClinicalRead<ReviewRow>
      key={JSON.stringify([ids.patientId, ids.encounterId])}
      resource="clinical-sections"
      {...ids}
    >
      {(rows) => {
        const review = rows.find((row) => row.section === "review");
        if (!review)
          return <p className="clinical-empty">{t("clinical.noDataPage")}</p>;
        if (!review.missing)
          return (
            <p className="clinical-empty">{t("clinical.sourceUnavailable")}</p>
          );
        return (
          <div className="clinical-review">
            <h3>{t("clinical.missingSections")}</h3>
            {review.missing.length ? (
              <ul>
                {[...new Set(review.missing)].map((section) => (
                  <li key={section}>
                    {SECTION_ORDER.includes(section as VisitSection)
                      ? t(sectionLabelKey(section as VisitSection))
                      : section}
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("clinical.noMissingReported")}</p>
            )}
          </div>
        );
      }}
    </PagedClinicalRead>
  );
}
