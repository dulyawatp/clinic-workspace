import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { VisitSection } from "../../domain/clinical";
import { sectionLabelKey } from "./sections";

export function SectionFrame({
  section,
  children,
}: {
  section: VisitSection;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="visit-frame">
      <p className="visit-frame-eyebrow">{t("clinical.workspace")}</p>
      <h2>{t(sectionLabelKey(section))}</h2>
      {children}
    </section>
  );
}
