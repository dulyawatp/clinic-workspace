import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  isValidVisitContext,
  type VisitContext,
  type VisitSection,
} from "../../domain/clinical";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { DiagnosesSection } from "./DiagnosesSection";
import { ExamSection } from "./ExamSection";
import { HistorySection } from "./HistorySection";
import { MedicationsSection } from "./MedicationsSection";
import { NoteSection } from "./NoteSection";
import { OverviewSection } from "./OverviewSection";
import { ReviewSection } from "./ReviewSection";
import { SectionFrame } from "./SectionFrame";
import { SECTION_ORDER, sectionLabelKey } from "./sections";
import { VisitHeader } from "./VisitHeader";
import { VisitSectionNav } from "./VisitSectionNav";

function subscribe(notify: () => void) {
  window.addEventListener("resize", notify);
  return () => window.removeEventListener("resize", notify);
}
function isMobile() {
  return window.innerWidth < 768;
}

function VisitDetail({ id }: { id: string }) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const mobile = useSyncExternalStore(subscribe, isMobile, () => false);
  const selected = params.get("section");
  const active: VisitSection =
    SECTION_ORDER.find((s) => s === selected) ?? "overview";
  const query = useReadQuery<VisitContext>("clinical-visits", {
    filters: { encounterId: id },
    limit: 1,
  });
  const change = (section: VisitSection) => {
    const next = new URLSearchParams(params);
    next.set("section", section);
    setParams(next);
  };
  if (query.isError)
    return (
      <StatePanel
        kind="error"
        title={t(toI18nKey(query.error))}
        onRetry={() => {
          void query.refetch();
        }}
        retryLabel={t("action.retry")}
      />
    );
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const context = query.data.items[0];
  if (!context || !isValidVisitContext(context) || context.encounterId !== id)
    return <StatePanel kind="empty" title={t("clinical.notFound")} />;
  const content = (section: VisitSection) => (
    <SectionFrame section={section}>
      {section === "overview" ? (
        <OverviewSection patientId={context.patientId} encounterId={id} />
      ) : section === "history" ? (
        <HistorySection patientId={context.patientId} encounterId={id} />
      ) : section === "exam" ? (
        <ExamSection patientId={context.patientId} encounterId={id} />
      ) : section === "note" ? (
        <NoteSection patientId={context.patientId} encounterId={id} />
      ) : section === "diagnoses" ? (
        <DiagnosesSection patientId={context.patientId} encounterId={id} />
      ) : section === "medications" ? (
        <MedicationsSection patientId={context.patientId} encounterId={id} />
      ) : section === "review" ? (
        <ReviewSection patientId={context.patientId} encounterId={id} />
      ) : (
        <p className="visit-placeholder">{t("clinical.sectionPending")}</p>
      )}
    </SectionFrame>
  );
  return (
    <div className="visit-workspace">
      <VisitHeader context={context} />
      {!mobile ? (
        <div className="visit-desktop">
          <VisitSectionNav active={active} onChange={change} />
          <div>
            {SECTION_ORDER.map((section) => (
              <div
                key={section}
                role="tabpanel"
                id={`visit-panel-${section}`}
                aria-labelledby={`visit-tab-${section}`}
                hidden={section !== active}
              >
                {section === active && content(section)}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="visit-accordion" data-testid="section-accordion">
          {SECTION_ORDER.map((section) => (
            <div className="visit-accordion-item" key={section}>
              <h2>
                <button
                  type="button"
                  aria-expanded={active === section}
                  aria-controls={`accordion-panel-${section}`}
                  onClick={() => change(section)}
                >
                  {t(sectionLabelKey(section))}
                  <span aria-hidden="true">
                    {active === section ? "−" : "+"}
                  </span>
                </button>
              </h2>
              <div
                id={`accordion-panel-${section}`}
                hidden={active !== section}
              >
                {active === section && content(section)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function VisitWorkspacePage() {
  const { t } = useTranslation();
  const { id } = useParams();
  return (
    <>
      <PageHeader
        title={t("clinical.title")}
        breadcrumb={<Link to={ROUTES.home}>{t("nav.today")}</Link>}
      />
      <div className="shell-page-body">
        {id ? (
          <VisitDetail id={id} />
        ) : (
          <StatePanel kind="empty" title={t("clinical.notFound")} />
        )}
      </div>
    </>
  );
}
