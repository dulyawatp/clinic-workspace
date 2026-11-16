import { type ReactNode, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import type { VisitSection } from "../../domain/clinical";
import { SECTION_ORDER, sectionLabelKey } from "./sections";
import { VisitSectionNav } from "./VisitSectionNav";

function subscribe(notify: () => void) {
  window.addEventListener("resize", notify);
  return () => window.removeEventListener("resize", notify);
}

function isMobile() {
  return window.innerWidth < 768;
}

/**
 * Desktop rail + panel (≥768px) or one-open accordion (<768px) around the
 * active visit section. The encounter identity stays above the layout, so it
 * never scrolls out of reach when a section changes.
 */
export function VisitSectionsLayout({
  active,
  onChange,
  render,
}: {
  active: VisitSection;
  onChange: (section: VisitSection) => void;
  render: (section: VisitSection) => ReactNode;
}) {
  const { t } = useTranslation();
  const mobile = useSyncExternalStore(subscribe, isMobile, () => false);
  return mobile ? (
    <div className="visit-accordion" data-testid="section-accordion">
      {SECTION_ORDER.map((section) => (
        <div className="visit-accordion-item" key={section}>
          <h2>
            <button
              type="button"
              aria-expanded={active === section}
              aria-controls={`accordion-panel-${section}`}
              onClick={() => onChange(section)}
            >
              {t(sectionLabelKey(section))}
              <span aria-hidden="true">{active === section ? "−" : "+"}</span>
            </button>
          </h2>
          <div id={`accordion-panel-${section}`} hidden={active !== section}>
            {active === section && render(section)}
          </div>
        </div>
      ))}
    </div>
  ) : (
    <div className="visit-desktop">
      <VisitSectionNav active={active} onChange={onChange} />
      <div>
        {SECTION_ORDER.map((section) => (
          <div
            key={section}
            role="tabpanel"
            id={`visit-panel-${section}`}
            aria-labelledby={`visit-tab-${section}`}
            hidden={section !== active}
          >
            {section === active && render(section)}
          </div>
        ))}
      </div>
    </div>
  );
}
