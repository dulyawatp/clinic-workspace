import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { z } from "zod";
import { useSession } from "../../app/SessionProvider";
import { type AppError, toI18nKey } from "../../domain/errors";
import { mapHttpError } from "../../transport/error-map";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { ROUTES } from "../../app/routes";
import { SampleBanner } from "../../design/components";
import { opsData } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { SurfaceIcon } from "../clinical/SurfaceIcon";
import { useOpsCopy } from "../operations/ops-copy";
import { itemName, purchaseOrdersFor, warehouseName } from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";
import "./pharmacy.css";

const text = z
  .string()
  .min(1)
  .max(256)
  .refine(
    (value) =>
      value.trim().length > 0 &&
      ![...value].some((char) => {
        const code = char.charCodeAt(0);
        return code < 32 || code === 127;
      }),
  );
const stockResponse = z
  .strictObject({
    items: z
      .array(
        z.strictObject({
          item_code: text,
          item_name: text,
          stock_uom: text,
          is_stock_item: z.boolean(),
        }),
      )
      .max(1000),
  })
  .refine(
    ({ items }) =>
      new Set(items.map((item) => item.item_code)).size === items.length,
  );

const unavailable = (): AppError => ({
  code: "UPSTREAM_UNAVAILABLE",
  retryable: true,
});

async function fetchStock(signal: AbortSignal) {
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch("/api/live/synthetic-stock", {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch {
    throw unavailable();
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401)
    throw { code: "AUTH_EXPIRED", retryable: false } satisfies AppError;
  if (response.status === 403)
    throw { code: "FORBIDDEN", retryable: false } satisfies AppError;
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  const parsed = stockResponse.safeParse(body);
  if (!parsed.success) throw unavailable();
  return parsed.data.items;
}

export function PharmacyPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { dateTime } = useOpsFormat();
  const [warehouse, setWarehouse] = useState("all");
  const { status, session, authGeneration, refresh } = useSession();
  const canViewPharmacy =
    status === "authenticated" &&
    session !== null &&
    session.user.roles.includes("PHA");
  const liveStockEnabled = canViewPharmacy && session.mode === "live";
  const query = useQuery<z.infer<typeof stockResponse>["items"], AppError>({
    queryKey: ["live-stock", session?.mode, authGeneration],
    enabled: liveStockEnabled,
    queryFn: ({ signal }) => fetchStock(signal),
    retry: false,
  });

  const stock = opsData.stockRows(warehouse === "all" ? undefined : warehouse);
  const expiring = opsData.expiryRows(
    warehouse === "all" ? undefined : warehouse,
    "30",
  );
  const openOrders = purchaseOrdersFor(warehouse, "open", "");
  const recentMovements = opsData.movements
    .filter((movement) => warehouse === "all" || movement.warehouse === warehouse)
    .slice(-8)
    .reverse();

  return (
    <>
      <PageHeader title={t("pharmacy.title")} />
      <div className="shell-page-body">
        {!canViewPharmacy ? (
          <StatePanel
            kind="permission-denied"
            title={t("state.denied.title")}
          />
        ) : liveStockEnabled ? (
          <section className="pharmacy-card" aria-labelledby="pharmacy-heading">
            <div className="pharmacy-intro">
              <span className="pharmacy-eyebrow">{t("pharmacy.eyebrow")}</span>
              <h2 id="pharmacy-heading">{t("pharmacy.heading")}</h2>
              <p>{t("pharmacy.description")}</p>
            </div>
            {query.isError ? (
              query.error.code === "AUTH_EXPIRED" || query.error.retryable ? (
                <StatePanel
                  kind="error"
                  title={t(toI18nKey(query.error))}
                  onRetry={() => {
                    if (query.error.code === "AUTH_EXPIRED") void refresh();
                    else void query.refetch();
                  }}
                  retryLabel={t("action.retry")}
                />
              ) : (
                <StatePanel kind="error" title={t(toI18nKey(query.error))} />
              )
            ) : query.isPending ||
              query.isFetching ||
              !query.isFetchedAfterMount ? (
              <StatePanel kind="loading" title={t("common.loading")} />
            ) : query.data.length === 0 ? (
              <StatePanel kind="empty" title={t("pharmacy.empty")} />
            ) : (
              <div className="pharmacy-table-wrap">
                <table className="pharmacy-table">
                  <caption className="shell-sr-only">
                    {t("pharmacy.heading")}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("pharmacy.name")}</th>
                      <th scope="col">{t("pharmacy.code")}</th>
                      <th scope="col">{t("pharmacy.unit")}</th>
                      <th scope="col">{t("pharmacy.type")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.map((item) => (
                      <tr key={item.item_code}>
                        <td data-label={t("pharmacy.name")}>
                          <strong>{item.item_name}</strong>
                        </td>
                        <td data-label={t("pharmacy.code")}>
                          {item.item_code}
                        </td>
                        <td data-label={t("pharmacy.unit")}>
                          {item.stock_uom}
                        </td>
                        <td data-label={t("pharmacy.type")}>
                          <span className="pharmacy-tag">
                            {t(
                              item.is_stock_item
                                ? "pharmacy.stockItem"
                                : "pharmacy.nonStockItem",
                            )}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ) : null}

        {canViewPharmacy ? (
          <>
            <SampleBanner
              label={t("app.demoTag")}
              title={copy(
                "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
                "Browser-local sample records · server contracts unverified",
              )}
            />
            <SourceNote />
            <FilterBar>
              <FilterField label={copy("คลัง", "Warehouse")}>
                <select
                  value={warehouse}
                  onChange={(event) => setWarehouse(event.target.value)}
                >
                  <option value="all">{copy("ทั้งหมด", "All")}</option>
                  {opsData.warehouses.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name[locale]}
                    </option>
                  ))}
                </select>
              </FilterField>
            </FilterBar>

            <section className="pc-kpis" aria-label={t("pharmacy.title")}>
              {[
                {
                  key: "stocked",
                  label: copy(
                    "รายการสินค้าและคลัง",
                    "Stocked item locations",
                  ),
                  value: stock.filter((row) => row.onHand > 0).length,
                  href: `${ROUTES.pharmacyBalances}?state=stocked`,
                },
                {
                  key: "low",
                  label: copy("ใกล้หมด", "Low stock"),
                  value: stock.filter((row) => row.low).length,
                  href: `${ROUTES.pharmacyBalances}?state=low`,
                },
                {
                  key: "out",
                  label: copy("หมดคลังที่ใช้ได้", "Out of usable stock"),
                  value: stock.filter((row) => row.out).length,
                  href: `${ROUTES.pharmacyBalances}?state=out`,
                },
                {
                  key: "expiry",
                  label: copy(
                    "หมดอายุภายใน 30 วัน",
                    "Expiring within 30 days",
                  ),
                  value: expiring.length,
                  href: `${ROUTES.pharmacyBatches}?window=30`,
                },
                {
                  key: "po",
                  label: copy("ใบสั่งซื้อที่ยังเปิด", "Open purchase orders"),
                  value: openOrders.length,
                  href: `${ROUTES.pharmacyPurchasing}?state=open`,
                },
              ].map((card) => (
                <Link
                  className="pc-kpi"
                  data-stock-kpi={card.key}
                  to={card.href}
                  key={card.key}
                >
                  <span className="pc-kpi__label">{card.label}</span>
                  <strong className="pc-kpi__value">{card.value}</strong>
                  <span className="pc-kpi__foot">
                    {warehouse === "all"
                      ? copy("ทั้งหมด", "All")
                      : warehouseName(warehouse, locale)}{" "}
                    · {copy("ภาพรวมปัจจุบัน", "Current snapshot")}
                  </span>
                </Link>
              ))}
            </section>

            <section className="pc-tilegrid" aria-label={t("pharmacy.title")}>
              {[
                {
                  icon: "chart",
                  label: copy("ยอดคงเหลือ", "Stock balance"),
                  to: ROUTES.pharmacyBalances,
                },
                {
                  icon: "layers",
                  label: copy("ล็อตและวันหมดอายุ", "Batch & expiry"),
                  to: ROUTES.pharmacyBatches,
                },
                {
                  icon: "download",
                  label: copy("รับเข้าคลัง", "Receive stock"),
                  to: ROUTES.pharmacyReceive,
                },
                {
                  icon: "transfer",
                  label: copy("โอนย้าย", "Transfer"),
                  to: ROUTES.pharmacyMovements,
                },
                {
                  icon: "truck",
                  label: copy("จัดซื้อ", "Purchasing"),
                  to: ROUTES.pharmacyPurchasing,
                },
                {
                  icon: "clipboard",
                  label: copy("บันทึกใช้จริง", "Actual usage"),
                  to: `${ROUTES.pharmacyMovements}?tab=usage`,
                },
              ].map((tile) => (
                <Link className="pc-tile" to={tile.to} key={tile.to}>
                  <span className="pc-tile__icon">
                    <SurfaceIcon name={tile.icon} />
                  </span>
                  <span className="pc-tile__label">{tile.label}</span>
                  <span className="pc-tile__meta">
                    {copy("ข้อมูลตัวอย่าง", "Demo data")}
                  </span>
                </Link>
              ))}
            </section>

            <section
              className="pharmacy-card"
              aria-labelledby="pharmacy-movements-heading"
            >
              <div className="pharmacy-intro">
                <h2 id="pharmacy-movements-heading">
                  {copy("การเคลื่อนไหวล่าสุด", "Recent stock movements")}
                </h2>
              </div>
              {recentMovements.length === 0 ? (
                <p className="pc-note">
                  {copy(
                    "ไม่พบรายการตามตัวกรอง",
                    "No records match these filters",
                  )}
                </p>
              ) : (
                recentMovements.map((movement) => (
                  <div className="pc-listrow" key={movement.id}>
                    <div className="pc-listrow__grow">
                      {itemName(movement.itemId, locale)}
                      <p className="pc-listrow__meta">
                        {dateTime(movement.at)} ·{" "}
                        <span className="ops-mono">
                          {movement.ref} · {movement.batchId}
                        </span>
                      </p>
                    </div>
                    <span>
                      {movement.qty > 0 ? "+" : ""}
                      {movement.qty}
                    </span>
                  </div>
                ))
              )}
            </section>

            <OpsNote>
              {copy(
                "จำนวนสินค้าเป็นรายการสินค้า–คลัง ใบสั่งซื้อไม่ระบุคลังจะอยู่ในทุกคลังเท่านั้น",
                "Stock counts use item–location records. POs without warehouse provenance appear only in All warehouses.",
              )}
            </OpsNote>
          </>
        ) : null}
      </div>
    </>
  );
}
