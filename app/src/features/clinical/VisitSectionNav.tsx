import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { VisitSection } from "../../domain/clinical";
import { SECTION_ORDER, sectionLabelKey } from "./sections";

export function VisitSectionNav({
  active,
  onChange,
}: {
  active: VisitSection;
  onChange: (section: VisitSection) => void;
}) {
  const { t } = useTranslation();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  return (
    <div
      className="visit-rail"
      role="tablist"
      aria-label={t("clinical.sections")}
      aria-orientation="vertical"
    >
      <p className="visit-rail-label" aria-hidden="true">
        {t("clinical.sections")}
      </p>
      {SECTION_ORDER.map((section, index) => (
        <button
          key={section}
          ref={(node) => {
            refs.current[index] = node;
          }}
          type="button"
          role="tab"
          id={`visit-tab-${section}`}
          aria-controls={`visit-panel-${section}`}
          aria-selected={section === active}
          tabIndex={section === active ? 0 : -1}
          onClick={() => onChange(section)}
          onKeyDown={(event) => {
            let next = index;
            if (event.key === "ArrowDown" || event.key === "ArrowRight")
              next = (index + 1) % SECTION_ORDER.length;
            else if (event.key === "ArrowUp" || event.key === "ArrowLeft")
              next = (index + SECTION_ORDER.length - 1) % SECTION_ORDER.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = SECTION_ORDER.length - 1;
            else return;
            event.preventDefault();
            const target = SECTION_ORDER[next];
            if (target) onChange(target);
            refs.current[next]?.focus();
          }}
        >
          <span className="visit-nav-number">
            {String(index + 1).padStart(2, "0")}
          </span>
          {t(sectionLabelKey(section))}
        </button>
      ))}
    </div>
  );
}
