import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RequireSession } from "../../app/guards";
import { getCapability } from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { PermissionDenied } from "../system/PermissionDenied";
import { SearchResultList } from "./SearchResultList";
import { SEARCH_PAGE_SIZE, usePatientSearch } from "./usePatientSearch";

function SearchResults({
  q,
  page,
  onPage,
  focusResults,
}: {
  q: string;
  page: number;
  onPage: (page: number) => void;
  focusResults: boolean;
}) {
  const { t } = useTranslation();
  const query = usePatientSearch(q, page);
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
  const previous = (
    <Button variant="secondary" onClick={() => onPage(page - 1)}>
      {t("button.previous")}
    </Button>
  );
  // Cached success is not confirmation for this observer. Suppress rows and
  // totals during every revalidation, including cached query/page returns.
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return (
      <>
        <StatePanel kind="loading" title={t("common.loading")} />
        {page > 1 && previous}
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
        {page > 1 && previous}
      </>
    );
  const pages = Math.max(1, Math.ceil(query.data.total / SEARCH_PAGE_SIZE));
  return (
    <>
      <SearchResultList items={query.data.items} focusOnMount={focusResults} />
      <nav className="search-pagination" aria-label={t("search.pagination")}>
        <Button
          variant="secondary"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          {t("button.previous")}
        </Button>
        <span role="status">{t("search.pageOf", { page, total: pages })}</span>
        <Button
          variant="secondary"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          {t("action.next")}
        </Button>
      </nav>
    </>
  );
}

function SearchContent() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const [text, setText] = useState("");
  const [settledText, setSettledText] = useState<string | null>("");
  const [page, setPage] = useState(1);
  const [focusResults, setFocusResults] = useState(false);
  const [composing, setComposing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const hintId = useId();

  useEffect(() => {
    if (composing) return;
    const timer = window.setTimeout(() => setSettledText(text), 300);
    return () => window.clearTimeout(timer);
  }, [text, composing]);

  function changeText(value: string) {
    setText(value);
    setSettledText(null);
    setPage(1);
    setFocusResults(false);
  }

  const capability = getCapability(caps, "read.patient");
  if (capability === "denied" || capability === "blocked_unverified")
    return <PermissionDenied resource="search" />;
  if (capability === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;

  return (
    <>
      <PageHeader title={t("surface.APP-03")} />
      <div className="shell-page-body">
        <section
          className="search-card"
          aria-labelledby="search-directory-title"
        >
          <h2 id="search-directory-title">{t("surface.PAT-01")}</h2>
          <search aria-label={t("search.globalLabel")}>
            <form
              className="search-toolbar"
              onSubmit={(event) => event.preventDefault()}
            >
              <div className="search-field">
                <label htmlFor={inputId}>{t("search.globalLabel")}</label>
                <input
                  ref={inputRef}
                  id={inputId}
                  type="search"
                  autoComplete="off"
                  value={text}
                  placeholder={t("search.globalPlaceholder")}
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
            </form>
          </search>
          <p id={hintId} className="search-hint">
            {t("search.hint")}
          </p>
          {/* Unmount old observer on every text edit, before debounce. The shared
          read hook consumes AbortSignal, so abandoned requests abort at once.
          Page key likewise disposes old observer instead of retaining its rows. */}
          {settledText !== null && !composing ? (
            <SearchResults
              key={page}
              q={settledText}
              page={page}
              focusResults={focusResults}
              onPage={(next) => {
                setFocusResults(true);
                setPage(next);
              }}
            />
          ) : (
            <StatePanel kind="loading" title={t("common.loading")} />
          )}
        </section>
      </div>
    </>
  );
}

export function SearchPage() {
  return (
    <RequireSession>
      <SearchContent />
    </RequireSession>
  );
}
