import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, Card, DataTable, PageHeader } from "../../design/components";
import type { TableColumn } from "../../design/components/DataTable";
import {
  type InventoryKind,
  InventoryReadError,
  type InventoryRows,
  readNativeInventory,
} from "../../transport/native/inventory-read";

const SURFACE = { items: "PHA-02", batches: "PHA-03", balances: "PHA-04", movements: "PHA-06" };
const PAGE_SIZE = 25; // Visible page size of the native read adapter.

export function NativeInventoryPage({
  kind, children,
}: { kind: InventoryKind; children?: ReactNode }) {
  const { t } = useTranslation();
  const { status, authGeneration } = useSession();
  return (
    <div data-surface={SURFACE[kind]} data-testid={`pharmacy-${kind}`}>
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{t("inventoryRead.home")}</Link>
            <span aria-hidden="true">/</span>
            <span>{SURFACE[kind]}</span>
          </>
        }
        eyebrow={t("inventoryRead.source")}
        title={t(`inventoryRead.${kind}.title`)}
      />
      <div className="pc-page__body">
        {status === "authenticated" ? (
          <InventoryView key={`${kind}:${authGeneration}`} kind={kind} />
        ) : (
          <Card title={t(`inventoryRead.${kind}.section`)}>
            {status === "loading" ? (
              <p role="status" className="pc-note">{t("common.loading")}</p>
            ) : (
              <p role="alert" className="pc-error">
                {t(status === "anonymous" ? "inventoryRead.permission" : "inventoryRead.unavailable")}
              </p>
            )}
          </Card>
        )}
        {status === "authenticated" && children}
      </div>
    </div>
  );
}

function InventoryView({ kind }: { kind: InventoryKind }) {
  const { t } = useTranslation();
  const col = (key: string, align?: "end"): TableColumn => ({
    key, label: t(`inventoryRead.column.${key}`), align,
  });
  const recordStatus = (disabled: number) => (
    <Badge tone={disabled ? "neutral" : "success"}>
      {t(`inventoryRead.${disabled ? "disabled" : "enabled"}`)}
    </Badge>
  );

  switch (kind) {
    case "items":
      return (
        <InventoryCard
          kind="items"
          columns={[col("item"), col("unit"), col("batchTracked"), col("status")]}
          cells={(row) => [
            <span key="item">
              <strong>{row.item_name}</strong><br />
              <span className="pc-note ops-mono">{row.item_code}</span>
            </span>,
            row.stock_uom,
            t(row.has_batch_no ? "inventoryRead.yes" : "inventoryRead.no"),
            recordStatus(row.disabled),
          ]}
        />
      );
    case "batches":
      return (
        <InventoryCard
          kind="batches"
          columns={[col("batch"), col("itemCode"), col("expiry"), col("status")]}
          cells={(row) => [
            row.batch_id, row.item, row.expiry_date || t("inventoryRead.noExpiry"), recordStatus(row.disabled),
          ]}
        />
      );
    case "balances":
      return (
        <InventoryCard
          kind="balances"
          columns={[col("itemCode"), col("warehouse"), col("unit"), col("actual", "end")]}
          cells={(row) => [row.item_code, row.warehouse, row.stock_uom, row.actual_qty]}
          actions={
            <Link className="pc-button pc-button--secondary pc-button--sm" to={ROUTES.pharmacyMovements}>
              {t("inventoryRead.movements.section")}
            </Link>
          }
        />
      );
    case "movements":
      return (
        <InventoryCard
          kind="movements"
          columns={[
            col("posting"), col("document"), col("itemCode"), col("warehouse"), col("change", "end"), col("after", "end"),
          ]}
          cells={(row) => [
            `${row.posting_date} ${row.posting_time}`,
            <span key="document">
              {row.voucher_type}<br />
              <span className="pc-note ops-mono">{row.voucher_no}</span>
            </span>,
            row.item_code, row.warehouse, `${row.actual_qty > 0 ? "+" : ""}${row.actual_qty}`, row.qty_after_transaction,
          ]}
        />
      );
  }
}

function InventoryCard<K extends InventoryKind>({
  kind, columns, cells, actions,
}: {
  kind: K;
  columns: TableColumn[];
  cells: (row: InventoryRows[K]) => ReactNode[];
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  const [request, setRequest] = useState({ offset: 0 });
  const [result, setResult] = useState<{
    request: { offset: number };
    outcome: { rows: InventoryRows[K][]; hasMore: boolean } | "permission" | "unavailable";
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void readNativeInventory(kind, request.offset, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) setResult({ request, outcome: page });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({
          request, outcome: error instanceof InventoryReadError ? error.kind : "unavailable",
        });
      },
    );
    return () => controller.abort();
  }, [kind, request]);

  const outcome = result?.request === request ? result.outcome : null;
  const page = outcome && typeof outcome === "object" ? outcome : null;
  const failed = typeof outcome === "string";
  const { offset } = request;
  const title = t(`inventoryRead.${kind}.section`);
  return (
    <Card
      title={title}
      hint={t(`inventoryRead.${kind}.hint`)}
      actions={actions}
      flush={page !== null}
      footer={offset > 0 || page?.hasMore ? (
        <>
          <button
            type="button"
            className="pc-button pc-button--secondary pc-button--sm"
            disabled={offset === 0}
            onClick={() => setRequest({ offset: offset - PAGE_SIZE })}
          >
            {t("inventoryRead.previous")}
          </button>
          <span className="pc-note" role="status">{t("inventoryRead.page", { page: offset / PAGE_SIZE + 1 })}</span>
          <button
            type="button"
            className="pc-button pc-button--secondary pc-button--sm"
            disabled={!page?.hasMore}
            onClick={() => setRequest({ offset: offset + PAGE_SIZE })}
          >
            {t("action.next")}
          </button>
        </>
      ) : undefined}
    >
      {page ? (
        <DataTable
          caption={title}
          columns={columns}
          rows={page.rows.map(cells)}
          empty={t(kind === "batches" ? "inventoryRead.batches.empty" : "inventoryRead.empty")}
        />
      ) : failed ? (
        <>
          <p role="alert" className="pc-error">{t(`inventoryRead.${outcome}`)}</p>
          <button
            type="button"
            className="pc-button pc-button--secondary pc-button--sm"
            onClick={() => setRequest({ offset })}
          >
            {t("action.retry")}
          </button>
        </>
      ) : <p role="status" className="pc-note">{t("common.loading")}</p>}
    </Card>
  );
}
