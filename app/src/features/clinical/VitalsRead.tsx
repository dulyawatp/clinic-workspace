import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";
import type { ClinicalIds, Vital } from "./ClinicalRead";
import { VitalsTable } from "./VitalsTable";

const PAGE_SIZE = 20;

export function VitalsRead({ patientId, encounterId }: ClinicalIds) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const offset = page * PAGE_SIZE;
  const query = useReadQuery<Vital>("clinical-vitals", {
    filters: { patientId, encounterId },
    limit: PAGE_SIZE,
    offset,
  });
  const loading =
    query.isPending || query.isFetching || !query.isFetchedAfterMount;
  const data = !loading && !query.isError ? query.data : undefined;
  const rows =
    data?.items.filter(
      (row) => row.patientId === patientId && row.encounterId === encounterId,
    ) ?? [];
  return (
    <>
      {query.isError ? (
        <StatePanel
          kind="error"
          title={t(toI18nKey(query.error))}
          onRetry={() => {
            void query.refetch();
          }}
          retryLabel={t("action.retry")}
        />
      ) : loading ? (
        <StatePanel kind="loading" title={t("common.loading")} />
      ) : (
        <VitalsTable vitals={rows} />
      )}
      {(page > 0 || (data && data.total > PAGE_SIZE)) && (
        <nav
          className="clinical-pagination"
          aria-label={t("clinical.vitalsPages")}
        >
          <button
            type="button"
            className="ui-button ui-button--secondary"
            disabled={page === 0 || loading}
            onClick={() => setPage((current) => current - 1)}
          >
            {t("clinical.previousPage")}
          </button>
          <span aria-live="polite">
            {t("clinical.page", { page: page + 1 })}
          </span>
          <button
            type="button"
            className="ui-button ui-button--secondary"
            disabled={!data || offset + PAGE_SIZE >= data.total}
            onClick={() => setPage((current) => current + 1)}
          >
            {t("clinical.nextPage")}
          </button>
        </nav>
      )}
    </>
  );
}
