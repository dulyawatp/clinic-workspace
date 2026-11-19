import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import {
  isValidVisitContext,
  type VisitContext,
  type VisitSection,
} from "../../domain/clinical";
import { toI18nKey } from "../../domain/errors";
import { coreData } from "../../fixtures/data";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { ApprovedVisitWorkspace } from "./ApprovedVisitWorkspace";
import { AuthoredVisitWorkspace } from "./AuthoredVisitWorkspace";
import { DiagnosesSection } from "./DiagnosesSection";
import { ExamSection } from "./ExamSection";
import { HistorySection } from "./HistorySection";
import { ExternalLabSection } from "./LabPanels";
import { MedicationsSection } from "./MedicationsSection";
import { NoteSection } from "./NoteSection";
import { OverviewSection } from "./OverviewSection";
import { ReviewSection } from "./ReviewSection";
import { SectionFrame } from "./SectionFrame";
import { SECTION_ORDER } from "./sections";
import { VisitHeader } from "./VisitHeader";
import { PhotosSection, ProceduresSection } from "./VisitSampleSections";
import { VisitSectionsLayout } from "./VisitSectionsLayout";

/**
 * DOC-02 addresses two sources on purpose:
 *  - an authored sample encounter (`DC-VIS-…`) renders the designed canonical
 *    editor demo, entirely local and network-free;
 *  - a read-model encounter (`ENC-…`) keeps the verified proxy read and its
 *    read-only sections, including the three designed sections that used to be
 *    placeholders.
 */
function ReadVisitDetail({ id }: { id: string }) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const selected = params.get("section");
  const active: VisitSection =
    SECTION_ORDER.find((section) => section === selected) ?? "overview";
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
      ) : section === "photos" ? (
        <PhotosSection patientId={context.patientId} />
      ) : section === "procedures" ? (
        <ProceduresSection patientId={context.patientId} />
      ) : (
        <ExternalLabSection patientId={context.patientId} />
      )}
    </SectionFrame>
  );
  return (
    <div className="visit-workspace">
      <VisitHeader context={context} />
      <VisitSectionsLayout active={active} onChange={change} render={content} />
    </div>
  );
}

function NativeVisitDetail({ id }: { id: string }) {
  const [params] = useSearchParams();
  const patientId = params.get("patient") ?? "";
  const appointment = params.get("appointment");
  const encounter = params.get("encounter");
  const alias = /^wse1_[0-9a-f]{32}_[0-9a-f]{16}_[A-Za-z0-9_-]{16,128}$/.test(
    encounter ?? "",
  )
    ? encounter
    : null;
  const appointmentAlias =
    appointment && /^wsa1_/.test(appointment) ? appointment : null;
  // A native draft needs either an existing server-derived encounter alias or
  // the server-derived appointment alias the draft will be opened against. The
  // read-only proxy context does not carry either, so an `ENC-` address without
  // them keeps the verified read view.
  if (!patientId || (!alias && !appointmentAlias)) return <ReadVisitDetail id={id} />;
  return (
    <AuthoredVisitWorkspace
      native={{
        patientId,
        appointmentId: appointmentAlias,
        encounter: alias,
        baseModified: params.get("modified"),
        state: params.get("state") === "completed" ? "completed" : "draft",
      }}
    />
  );
}

function VisitDetail({ id }: { id: string }) {
  const { t } = useTranslation();
  const { session, status } = useSession();
  if (id.startsWith("DC-VIS-")) {
    if (status === "loading") return <StatePanel kind="loading" title={t("common.loading")} />;
    const authored = session && session.mode !== "live" ? coreData.visitById(id) : null;
    return authored
      ? <ApprovedVisitWorkspace visit={authored} />
      : <StatePanel kind="empty" title={t("clinical.notFound")} />;
  }
  if (id.startsWith("ENC-")) return <NativeVisitDetail id={id} />;
  return <ReadVisitDetail id={id} />;
}

export function VisitWorkspacePage() {
  const { t } = useTranslation();
  const { id } = useParams();
  return (
    <>
      <PageHeader
        title={t("surface.DOC-02")}
        breadcrumb={<Link to={ROUTES.home}>{t("nav.today")}</Link>}
      />
      <div className="shell-page-body">
        {id ? (
          <VisitDetail key={id} id={id} />
        ) : (
          <StatePanel kind="empty" title={t("clinical.notFound")} />
        )}
      </div>
    </>
  );
}
