import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";
import type { ClinicalIds } from "./ClinicalRead";

const PAGE_SIZE = 20;

export function PagedClinicalRead<T extends ClinicalIds>({
  resource,
  patientId,
  encounterId,
  children,
}: ClinicalIds & {
  resource: string;
  children: (rows: T[], total: number) => ReactNode;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const offset = page * PAGE_SIZE;
  const query = useReadQuery<T>(resource, {
    filters: { patientId, encounterId },
    limit: PAGE_SIZE,
    offset,
  });
  const loading =
    query.isPending || query.isFetching || !query.isFetchedAfterMount;
  const data = !loading && !query.isError ? query.data : undefined;
  const rows =
    data?.items.filter(
      (row) => row.encounterId === encounterId && row.patientId === patientId,
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
        data && children(rows, data.total)
      )}
      {(page > 0 || (data && data.total > PAGE_SIZE)) && (
        <nav
          className="clinical-pagination"
          aria-label={t("clinical.resultPages")}
        >
          <button
            type="button"
            className="ui-button ui-button--secondary"
            disabled={page === 0 || loading}
            onClick={() => setPage((n) => n - 1)}
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
            onClick={() => setPage((n) => n + 1)}
          >
            {t("clinical.nextPage")}
          </button>
        </nav>
      )}
    </>
  );
}
