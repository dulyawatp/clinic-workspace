import { useTranslation } from "react-i18next";
import { translateClinicalText } from "../../i18n";
import type { ClinicalIds } from "./ClinicalRead";
import { DraftStatusBar } from "./DraftStatusBar";
import { PagedClinicalRead } from "./PagedClinicalRead";

interface NoteRow extends ClinicalIds {
  section: string;
  state?: string;
  savedAt?: string | null;
  assessment?: string;
  plan?: string;
}
export function NoteSection(ids: ClinicalIds) {
  const { t } = useTranslation();
  return (
    <PagedClinicalRead<NoteRow>
      key={JSON.stringify([ids.patientId, ids.encounterId])}
      resource="clinical-sections"
      {...ids}
    >
      {(rows) => {
        const note = rows.find((row) => row.section === "note");
        if (!note)
          return <p className="clinical-empty">{t("clinical.noDataPage")}</p>;
        return (
          <>
            <p className="clinical-notice">
              {t("clinical.compositionUnverified")}
            </p>
            <DraftStatusBar state={note.state} savedAt={note.savedAt} />
            <dl className="clinical-fields">
              <div>
                <dt>{t("clinical.assessment")}</dt>
                <dd>
                  {note.assessment
                    ? translateClinicalText(note.assessment)
                    : t("clinical.sourceUnavailable")}
                </dd>
              </div>
              <div>
                <dt>{t("clinical.plan")}</dt>
                <dd>
                  {note.plan
                    ? translateClinicalText(note.plan)
                    : t("clinical.sourceUnavailable")}
                </dd>
              </div>
              <div>
                <dt>{t("clinical.rationale")}</dt>
                <dd>{t("clinical.sourceUnavailable")}</dd>
              </div>
              <div>
                <dt>{t("clinical.followup")}</dt>
                <dd>{t("clinical.sourceUnavailable")}</dd>
              </div>
            </dl>
          </>
        );
      }}
    </PagedClinicalRead>
  );
}
