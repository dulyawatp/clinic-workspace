import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import { useCapabilities } from "../shell/CapabilityProvider";
import { useEncounterDraft } from "./EncounterDraft";
import { useLivePatients } from "./live-directory";
import { PatientRow } from "./PatientRow";
import { PatientWriteForm } from "./PatientWriteForm";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { PATIENTS_PAGE_SIZE, usePatients } from "./usePatients";

function DirectoryRows({
  q,
  page,
  onPage,
}: {
  q: string;
  page: number;
  onPage: (page: number) => void;
}) {
  const { t } = useTranslation();
  const query = usePatients({ q, page });

  // A pending or empty page must still let the reader go back: without this,
  // page 2 of an empty/moving result set is a dead end.
  const previous =
    page > 1 ? (
      <div className="patients-pagination-row">
        <Button variant="secondary" onClick={() => onPage(page - 1)}>
          {t("button.previous")}
        </Button>
      </div>
    ) : null;

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
  // Cached success is not confirmation for this observer: rows and totals stay
  // hidden during every revalidation, including cached query/page returns.
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return (
      <>
        <StatePanel kind="loading" title={t("common.loading")} />
        {previous}
      </>
    );
  if (query.data.items.length === 0)
    return (
      <>
        <StatePanel
          kind="empty"
          title={t("search.noResults")}
          detail={t("search.noResultsBody")}
        />
        {previous}
      </>
    );

  const pages = Math.max(1, Math.ceil(query.data.total / PATIENTS_PAGE_SIZE));
  return (
    <>
      <div className="patients-table-wrap">
        <table className="patients-table">
          <caption className="shell-sr-only">{t("surface.PAT-01")}</caption>
          <thead>
            <tr>
              <th id="directory-name" scope="col">
                {t("search.patientName")}
              </th>
              <th id="directory-id" scope="col">
                {t("search.patientId")}
              </th>
              <th id="directory-phone" scope="col">
                {t("search.phone")}
              </th>
              <th id="directory-last-visit" scope="col">
                {t("search.lastVisit")}
              </th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((patient) => (
              <PatientRow key={patient.id} patient={patient} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="patients-pagination-row">
        <p className="patients-total" role="status">
          {t("patients.total", { total: query.data.total })}
        </p>
        <nav
          className="patients-pagination"
          aria-label={t("search.pagination")}
        >
          <Button
            variant="secondary"
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
          >
            {t("button.previous")}
          </Button>
          <span>{t("search.pageOf", { page, total: pages })}</span>
          <Button
            variant="secondary"
            disabled={page >= pages}
            onClick={() => onPage(page + 1)}
          >
            {t("action.next")}
          </Button>
        </nav>
      </div>
    </>
  );
}

function LiveDirectoryRows({
  q,
  page,
  onPage,
  onDraft,
}: {
  q: string;
  page: number;
  onPage: (page: number) => void;
  onDraft?: (patient: {
    display_name: string;
    workspace_patient_id: string;
  }) => void;
}) {
  const { t } = useTranslation();
  const { refresh } = useSession();
  const query = useLivePatients();
  const search = q.trim().toLocaleLowerCase();
  const filtered = (query.data ?? []).filter(
    (patient) =>
      patient.display_name.toLocaleLowerCase().includes(search) ||
      patient.workspace_patient_id.toLocaleLowerCase().includes(search),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PATIENTS_PAGE_SIZE));
  useEffect(() => {
    if (query.isSuccess && !query.isFetching && page > pages) onPage(pages);
  }, [query.isSuccess, query.isFetching, page, pages, onPage]);
  if (query.isError) {
    if (query.error.code === "AUTH_EXPIRED")
      return (
        <StatePanel
          kind="error"
          title={t(toI18nKey(query.error))}
          onRetry={() => {
            void refresh();
          }}
          retryLabel={t("action.retry")}
        />
      );
    if (!query.error.retryable)
      return <StatePanel kind="error" title={t(toI18nKey(query.error))} />;
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
  }
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const currentPage = Math.min(page, pages);
  const items = filtered.slice(
    (currentPage - 1) * PATIENTS_PAGE_SIZE,
    currentPage * PATIENTS_PAGE_SIZE,
  );
  return (
    <>
      {items.length === 0 ? (
        <StatePanel
          kind="empty"
          title={t(search ? "patients.liveNoMatches" : "patients.liveEmpty")}
        />
      ) : (
        <div className="patients-table-wrap">
          <table className="patients-table">
            <caption className="shell-sr-only">{t("surface.PAT-01")}</caption>
            <thead>
              <tr>
                <th id="directory-name" scope="col">
                  {t("search.patientName")}
                </th>
                <th id="directory-reference" scope="col">
                  {t("patients.workspaceReference")}
                </th>
                {onDraft && <th scope="col">{t("encounterDraft.actions")}</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((patient) => (
                <tr className="patients-row" key={patient.workspace_patient_id}>
                  <td
                    headers="directory-name"
                    data-label={t("search.patientName")}
                    className="patients-name"
                  >
                    <div>
                      <span className="patients-name-text">
                        {patient.display_name}
                      </span>
                      <Link
                        className="patients-open"
                        to={ROUTES.patient(patient.workspace_patient_id)}
                        aria-label={`${t("action.openRecord")} · ${patient.display_name} · ${patient.workspace_patient_id}`}
                      >
                        {t("action.openRecord")}
                      </Link>
                    </div>
                  </td>
                  <td
                    headers="directory-reference"
                    data-label={t("patients.workspaceReference")}
                    className="patients-id"
                  >
                    {patient.workspace_patient_id}
                  </td>
                  {onDraft && (
                    <td data-label={t("encounterDraft.actions")}>
                      <Button
                        variant="secondary"
                        onClick={() => onDraft(patient)}
                        aria-label={t("encounterDraft.select", {
                          name: patient.display_name,
                          reference: patient.workspace_patient_id,
                        })}
                      >
                        {t("encounterDraft.title")}
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="patients-pagination-row">
        <p className="patients-total" role="status">
          {t("patients.total", { total: filtered.length })}
        </p>
        <nav
          className="patients-pagination"
          aria-label={t("search.pagination")}
        >
          <Button
            variant="secondary"
            disabled={currentPage <= 1}
            onClick={() => onPage(currentPage - 1)}
          >
            {t("button.previous")}
          </Button>
          <span>{t("search.pageOf", { page: currentPage, total: pages })}</span>
          <Button
            variant="secondary"
            disabled={currentPage >= pages}
            onClick={() => onPage(currentPage + 1)}
          >
            {t("action.next")}
          </Button>
        </nav>
      </div>
    </>
  );
}

function DirectoryContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const navigate = useNavigate();
  const live = session?.mode === "live";
  const native = nativeTransportEnabled();
  const nativeLive = native && live;
  const [text, setText] = useState("");
  const [settledText, setSettledText] = useState<string | null>("");
  const [page, setPage] = useState(1);
  const [composing, setComposing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const hintId = useId();
  const canDraft =
    !native &&
    live &&
    session.user.roles.includes("DOC") &&
    getCapability(caps, "write.encounter.draft") === "enabled";
  const draft = useEncounterDraft(canDraft ? session?.user.id : undefined);

  useEffect(() => {
    if (composing) return;
    const timer = window.setTimeout(() => setSettledText(text), 300);
    return () => window.clearTimeout(timer);
  }, [text, composing]);

  function changeText(value: string) {
    setText(value);
    setSettledText(null);
    setPage(1);
  }

  const directoryHeader = (
    <PageHeader
      title={t("surface.PAT-01")}
      actions={nativeLive && (
        <Link className="ui-button ui-button--primary" to={ROUTES.nativeEncounterNew}>
          {t("nativeEncounter.launch")}
        </Link>
      )}
    />
  );

  const capability = getCapability(caps, "read.patient");
  const listingDenied =
    (live &&
      !session.user.roles.some(
        (role) => role === "DOC" || role === "REC" || role === "NUR",
      )) ||
    capability === "denied" ||
    capability === "blocked_unverified";
  if (listingDenied || capability === "error_upstream")
    return (
      <>
        {nativeLive && directoryHeader}
        <div className="shell-page-body">
          {listingDenied ? (
            <StatePanel
              kind="permission-denied"
              title={t("state.denied.title")}
              detail={t("state.denied.body", { resource: t("surface.PAT-01") })}
            />
          ) : (
            <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
          )}
        </div>
      </>
    );

  return (
    <>
      {directoryHeader}
      <div className="shell-page-body">
        <section
          className="patients-card"
          aria-labelledby="patients-directory-title"
        >
          <h2 id="patients-directory-title">{t("surface.PAT-01")}</h2>
          <form
            className="patients-toolbar"
            onSubmit={(event) => event.preventDefault()}
          >
            <div className="patients-field">
              <label htmlFor={inputId}>{t("patients.searchLabel")}</label>
              <input
                ref={inputRef}
                id={inputId}
                type="search"
                autoComplete="off"
                value={text}
                placeholder={t(
                  live
                    ? "patients.liveSearchPlaceholder"
                    : "search.globalPlaceholder",
                )}
                aria-describedby={hintId}
                onChange={(event) => changeText(event.target.value)}
                onCompositionStart={() => {
                  setComposing(true);
                  setSettledText(null);
                }}
                onCompositionEnd={() => setComposing(false)}
              />
            </div>
            {text !== "" && (
              <Button
                variant="secondary"
                onClick={() => {
                  changeText("");
                  inputRef.current?.focus();
                }}
              >
                {t("search.clear")}
              </Button>
            )}
            {!live && (
              <WriteGate
                capability="write.patient.create"
                reason={t("patients.registerBlocked")}
              >
                <Button onClick={() => navigate(ROUTES.patientNew)}>
                  {t("action.register")}
                </Button>
              </WriteGate>
            )}
          </form>
          <p id={hintId} className="patients-hint">
            {t(live ? "patients.liveSearchHint" : "patients.searchHint")}
          </p>
          {live && nativeTransportEnabled() && getCapability(caps, "write.patient.create") === "enabled" && <PatientWriteForm />}
          {canDraft && draft.panel}
          {settledText !== null && !composing ? (
            live ? (
              <LiveDirectoryRows
                q={settledText}
                page={page}
                onPage={setPage}
                onDraft={canDraft ? draft.select : undefined}
              />
            ) : (
              <DirectoryRows
                key={`${settledText}#${page}`}
                q={settledText}
                page={page}
                onPage={setPage}
              />
            )
          ) : (
            <StatePanel kind="loading" title={t("common.loading")} />
          )}
        </section>
      </div>
    </>
  );
}

export function PatientDirectoryPage() {
  return (
    <RequireSession>
      <DirectoryContent />
    </RequireSession>
  );
}
