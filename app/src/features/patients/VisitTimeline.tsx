import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, SampleBanner } from "../../design/components";
import { coreData } from "../../fixtures/data";
import { formatDate, formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { type EncounterReadError, listNativeEncounters, type NativeEncounterPage } from "../../transport/native/encounter-read";
import { Button } from "../../ui/Button";
import { StatePanel } from "../../ui/StatePanel";
import { EncounterReadFailure, EncounterStatus } from "../clinical/ApprovedVisitWorkspace";
import { LabReportDisclosure } from "../clinical/NativeLabReports";
import { SampleLabFiles, SamplePhotos } from "../clinical/VisitEvidencePreview";
import { approvedVisitNarrative, SAMPLE_REPORT, sampleLabFiles, samplePhotoSides, sampleVisitHistory, type SampleHistoryPreview } from "../clinical/visit-preview";

export function NativeVisitTimeline({ patientRef }: { patientRef: string }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { authGeneration } = useSession();
  const key = ["native-visit-history", authGeneration, patientRef] as const;
  const query = useInfiniteQuery<NativeEncounterPage, EncounterReadError, InfiniteData<NativeEncounterPage>, typeof key, number>({
    queryKey: key,
    queryFn: ({ pageParam, signal }) => listNativeEncounters(patientRef, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: ({ nextOffset }) => nextOffset,
    retry: false,
    gcTime: 0,
  });
  const visits = query.data?.pages.flatMap(({ items }) => items) ?? [];
  const patient = visits[0];
  const loading = query.isPending || !query.isFetchedAfterMount || (query.isFetching && !query.isFetchingNextPage);

  return (
    <section className="visit-workspace visit-preview" aria-label={t("workspace.history")}>
      {!loading && !query.isError && patient && (
        <header className="visit-identity visit-preview-identity">
          <div className="visit-preview-head">
            <div className="visit-identity-heading">
              <span className="visit-avatar" aria-hidden="true">✚</span>
              <div><p>{t("clinical.patient")}</p><h2>{patient.patientName}</h2></div>
            </div>
            <Badge tone="info">{t("clinical.historyReadOnly")}</Badge>
          </div>
        </header>
      )}
      <div className="visit-preview-head visit-history-heading">
        <div>
          <p>{t("workspace.history")}</p>
          <h2 id="visit-timeline-heading">{t("workspace.timeline")}</h2>
        </div>
      </div>
      {query.isError ? (
        <EncounterReadFailure error={query.error} onRetry={() => void query.refetch()} />
      ) : loading ? (
        <StatePanel kind="loading" title={t("common.loading")} />
      ) : !visits.length && !query.hasNextPage ? (
        <StatePanel kind="empty" title={t("workspace.nativeHistoryEmpty")} />
      ) : (
        <>
          {visits.length > 0 && (
            <ol role="list" className="visit-history-list" aria-labelledby="visit-timeline-heading">
              {visits.map((visit) => (
                <li key={visit.id} className="visit-history-entry">
                  {visit.encounterDate ? (
                    <time className="visit-history-date" dateTime={visit.encounterDate}>{formatDate(visit.encounterDate, locale)}</time>
                  ) : (
                    <span className="visit-history-date">{t("common.unavailable")}</span>
                  )}
                  <div className="visit-history-spine" aria-hidden="true" />
                  <article className="visit-preview-card visit-history-card visit-native-history-card"
                    aria-labelledby={`native-history-${visit.id}`}>
                    <div className="visit-history-intro visit-preview-stack">
                      <div className="visit-preview-head">
                        <h3 id={`native-history-${visit.id}`}>{t("workspace.historyVisit", { id: visit.id })}</h3>
                        <EncounterStatus status={visit.docstatus} />
                      </div>
                      <p className="pc-note">{t("clinical.practitioner")}: {visit.practitioner || t("common.unavailable")}</p>
                    </div>
                    <div className="visit-history-report">
                      <LabReportDisclosure encounterId={visit.id} />
                    </div>
                    <div className="visit-history-evidence visit-preview-stack">
                      <h4>{t("workspace.photos")}</h4>
                      <p className="pc-note">{t("workspace.nativePhotosUnavailable")}</p>
                    </div>
                    <div className="visit-history-narrative visit-preview-stack">
                      <details className="visit-preview-details">
                        <summary>{t("workspace.readNote")}</summary>
                        <p className="pc-note">{t("workspace.savedNoteUnavailable")}</p>
                      </details>
                    </div>
                  </article>
                </li>
              ))}
            </ol>
          )}
          {query.hasNextPage && (
            <div className="pc-row">
              <Button variant="secondary" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {t(query.isFetchingNextPage ? "common.loading" : "workspace.nativeHistoryMore")}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function VisitTimeline({ patientId, encounterId }: { patientId: string; encounterId: string | null }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  // Only this explicitly fictional page consumes the preview carried from its
  // originating encounter. It never goes into a read request or native view.
  const preview = (useLocation().state as { historyPreview?: SampleHistoryPreview } | null)?.historyPreview;
  const visits = sampleVisitHistory(patientId);
  const candidate = encounterId ? coreData.visitById(encounterId) : null;
  const returnVisit = candidate?.patientId === patientId ? candidate : null;

  return (
    <div className="visit-workspace visit-preview">
      <SampleBanner label={t("app.demoTag")} title={t("workspace.timeline")}>
        <p className="pc-note">{t("workspace.historyScope")}</p>
      </SampleBanner>
      <header className="visit-identity visit-preview-identity">
        <div className="visit-preview-head">
          <div className="visit-identity-heading">
            <span className="visit-avatar" aria-hidden="true">✚</span>
            <div><p>{t("clinical.patient")} · {patientId}</p><h2>{coreData.patientName(patientId, locale)}</h2></div>
          </div>
          {returnVisit && <Link className="pc-button pc-button--secondary" to={ROUTES.visit(returnVisit.id)}>{t("workspace.backEncounter")}</Link>}
        </div>
      </header>
      <div className="visit-preview-head visit-history-heading">
        <div>
          <p>{t("workspace.history")}</p>
          <h2 id="visit-timeline-heading">{t("workspace.timeline")}</h2>
        </div>
        <p className="pc-note">{t(visits.length === 1 ? "workspace.oneVisit" : "workspace.visitCount", { count: visits.length })}</p>
      </div>
      <ol role="list" className="visit-history-list" aria-labelledby="visit-timeline-heading">
        {visits.map((visit) => {
          const local = visit.id === encounterId && visit.id === preview?.visitId ? preview : null;
          const files = sampleLabFiles(visit);
          if (local?.reportAdded === true) files.push(SAMPLE_REPORT);
          const sides = Array.isArray(local?.sides)
            ? local.sides.filter((side) => side === "before" || side === "after")
            : samplePhotoSides(visit);
          const narrative = typeof local?.savedNote === "string" ? local.savedNote : approvedVisitNarrative(visit, t);
          return (
            <li key={visit.id} className="visit-history-entry">
              <time className="visit-history-date" dateTime={visit.date}>{formatDate(visit.date, locale)}</time>
              <div className="visit-history-spine" aria-hidden="true" />
              <article className="visit-preview-card visit-history-card" data-photos={sides.length > 0} aria-labelledby={`history-${visit.id}`}>
                <div className="visit-history-intro visit-preview-stack">
                  <div className="visit-preview-head">
                    <h3 id={`history-${visit.id}`}>{t("workspace.historyVisit", { id: visit.id })}</h3>
                    <Badge tone={visit.state === "completed" ? "success" : "warning"}>{t(`status.${visit.state}`)}</Badge>
                  </div>
                  <p className="pc-note">{formatDateTime(visit.date, locale)} · {coreData.practitionerById(visit.practitioner).name[locale]}</p>
                </div>
                {files.length > 0 && <div className="visit-history-report visit-preview-stack">
                  <h4>{t("workspace.lab")} · {t("workspace.privateAttachment")}</h4>
                  <SampleLabFiles files={files} />
                </div>}
                {sides.length > 0 && <div className="visit-history-evidence visit-preview-stack">
                  <h4>{t("workspace.photos")}</h4>
                  <SamplePhotos sides={sides} />
                </div>}
                <div className="visit-history-narrative visit-preview-stack">
                  {visit.diagnoses.length > 0 && <p>{visit.diagnoses.map((row) => row.label[locale]).join(" · ")}</p>}
                  {narrative && (
                    <details className="visit-preview-details">
                      <summary>{t("workspace.readNote")}</summary>
                      <p className="visit-history-note">{narrative}</p>
                    </details>
                  )}
                  {sides.length === 0 && files.length === 0 && <p className="pc-note">{t("workspace.noFiles")}</p>}
                </div>
              </article>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
