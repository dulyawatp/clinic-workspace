import { useTranslation } from "react-i18next";
import { Badge } from "../../design/components";
import type { RoleCode } from "../../domain/capability";
import { clinicalRedactionNotices } from "../../fixtures/data-clinical";
import { useLocale } from "../../i18n/LocaleProvider";

const SECTION_KEYS: Readonly<Record<string, string>> = {
  diagnoses: "redaction.section.diagnoses",
  note: "redaction.section.note",
  labs: "redaction.section.labs",
};

/** `note.assessment` is still the visit-note section. */
function sectionKey(section: string): string {
  const base = section.split(".")[0] ?? section;
  return SECTION_KEYS[base] ?? "redaction.section.unknown";
}

/**
 * PAT-03 per-role clinical-redaction notice. It states what the held role may
 * read and which sections stay closed; the notice rows themselves are authored
 * Demo/Sample content, so the block is labelled as such. Presentation only —
 * the proxy/native read matrix remains the gate.
 */
export function RedactionNotice({ roles }: { roles: readonly RoleCode[] }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  if (roles.length === 0) return null;
  const notice = clinicalRedactionNotices.find((row) =>
    roles.includes(row.role),
  );
  // A role the authored notice set does not list reads the same restricted
  // scope without the sample section list: no role loses the explanation.
  const seesClinicalDetail = notice?.seesClinicalDetail ?? false;
  const sections =
    notice?.redactedSections.map((section) => t(sectionKey(section))) ?? [];
  return (
    <section
      className="pc-banner pc-banner--demo patients-redaction"
      aria-labelledby="patients-redaction-title"
      data-testid="redaction-notice"
    >
      <div className="pc-banner__body">
        <div className="pc-banner__title">
          {notice !== undefined && (
            <Badge tone="demo">{t("app.demoTag")}</Badge>
          )}
          <h2 id="patients-redaction-title">{t("redaction.title")}</h2>
        </div>
        <p>
          {seesClinicalDetail
            ? t("redaction.full")
            : t("redaction.restricted")}
        </p>
        {notice !== undefined && <p>{notice.note[locale]}</p>}
        {seesClinicalDetail ? (
          <p>{t("redaction.role", { role: notice?.role ?? roles[0] })}</p>
        ) : sections.length > 0 ? (
          <p>{t("redaction.sections", { sections: sections.join(", ") })}</p>
        ) : (
          <p>{t("redaction.role", { role: roles[0] })}</p>
        )}
      </div>
    </section>
  );
}
