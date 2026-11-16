import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { opsData } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  expiryBucketCounts,
  expiryRowsFor,
  itemName,
  warehouseName,
} from "../operations/ops-model";
import {
  Buckets,
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const WINDOWS = ["all", "expired", "7", "30", "90"] as const;
type Window = (typeof WINDOWS)[number];

/**
 * PHA-03 batches and expiry. The filter windows are cumulative (30 includes 7),
 * while the bucket strip counts exclusive windows straight from the helper's
 * `urgency` value, so the five buckets always add up to the row total. Usable
 * quantity excludes expired lots; physical quantity does not.
 */
export function PharmacyBatchesPage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { date } = useOpsFormat();
  const [params] = useSearchParams();
  const initialWarehouse = params.get("warehouse") ?? "all";
  const initialWindow = params.get("window");
  const [warehouse, setWarehouse] = useState(
    opsData.warehouses.some((row) => row.id === initialWarehouse)
      ? initialWarehouse
      : "all",
  );
  const [window, setWindow] = useState<Window>(
    WINDOWS.includes(initialWindow as Window) ? (initialWindow as Window) : "all",
  );
  const [search, setSearch] = useState("");

  const allRows = useMemo(() => expiryRowsFor(warehouse, "all"), [warehouse]);
  const buckets = useMemo(() => expiryBucketCounts(allRows), [allRows]);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return expiryRowsFor(warehouse, window).filter((row) => {
      if (!needle) return true;
      return `${row.batchId} ${itemName(row.itemId, "th")} ${itemName(row.itemId, "en")}`
        .toLowerCase()
        .includes(needle);
    });
  }, [warehouse, window, search]);

  return (
    <div data-surface="PHA-03" data-testid="pharmacy-batches">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>PHA-03</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ล็อตและวันหมดอายุ", "Batches & expiry")}
      />
      <div className="pc-page__body">
        <SampleBanner
          label={copy("ข้อมูลตัวอย่าง", "Demo data")}
          title={copy(
            "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
            "Browser-local sample records · server contracts unverified",
          )}
        />
        <SourceNote />

        <Card
          title={copy(
            "ล็อตที่มียอดคงเหลือจริง",
            "Positive physical stock batches",
          )}
          hint={copy(
            "ตัวกรอง 7/30/90 วันรวมช่วงสั้นกว่า แต่ไม่นับล็อตหมดอายุ · จำนวนช่วงด้านล่างไม่ซ้อนกัน · ใช้ได้ถึงวันหมดอายุตามเวลาไทย",
            "7/30/90 filters are cumulative, exclude expired lots. Counts below use exclusive buckets. Usable through expiry date in Bangkok.",
          )}
          flush
        >
          <div className="ops-card-tools">
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
              <FilterField label={copy("ช่วงหมดอายุ", "Expiry window")}>
                <select
                  value={window}
                  onChange={(event) =>
                    setWindow(event.target.value as Window)
                  }
                >
                  <option value="all">{copy("ทั้งหมด", "All")}</option>
                  <option value="expired">
                    {copy("หมดอายุแล้ว", "Expired")}
                  </option>
                  <option value="7">
                    {copy("ภายใน 7 วัน", "Within 7 days")}
                  </option>
                  <option value="30">
                    {copy("ภายใน 30 วัน", "Within 30 days")}
                  </option>
                  <option value="90">
                    {copy("ภายใน 90 วัน", "Within 90 days")}
                  </option>
                </select>
              </FilterField>
              <FilterField label={copy("ค้นหารายการ / ล็อต", "Search item / batch")}>
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </FilterField>
            </FilterBar>
            <Buckets
              label={copy("ช่วงหมดอายุ", "Expiry window")}
              entries={[
                {
                  key: "expired",
                  label: copy("หมดอายุ", "Expired"),
                  value: buckets.expired,
                },
                {
                  key: "7",
                  label: copy("0–7 วัน", "0–7 days"),
                  value: buckets["7"],
                },
                {
                  key: "30",
                  label: copy("8–30 วัน", "8–30 days"),
                  value: buckets["30"],
                },
                {
                  key: "90",
                  label: copy("31–90 วัน", "31–90 days"),
                  value: buckets["90"],
                },
                {
                  key: "later",
                  label: copy("มากกว่า 90 วัน", "Over 90 days"),
                  value: buckets.later,
                },
              ]}
            />
          </div>
          <DataTable
            caption={copy(
              "ล็อตที่มียอดคงเหลือจริง",
              "Positive physical stock batches",
            )}
            columns={[
              {
                key: "item",
                label: copy("รายการ / ล็อต", "Item / batch"),
              },
              { key: "warehouse", label: copy("คลัง", "Warehouse") },
              {
                key: "expiry",
                label: copy(
                  "วันหมดอายุ / วันคงเหลือ",
                  "Expiry / days remaining",
                ),
              },
              {
                key: "balance",
                label: copy("คงเหลือจริง / ใช้ได้", "Physical / usable"),
                align: "end",
              },
              { key: "status", label: copy("สถานะ", "Status") },
            ]}
            rows={rows.map((row) => [
              <span key="item">
                {itemName(row.itemId, locale)}
                <br />
                <span className="pc-note ops-mono">{row.batchId}</span>
              </span>,
              warehouseName(row.warehouse, locale),
              <span key="expiry">
                {date(row.expiry)}
                <br />
                <span className="pc-note">
                  {row.daysRemaining ?? "—"}{" "}
                  {copy("วัน", "days")}
                </span>
              </span>,
              `${row.onHand} / ${row.usableQty}`,
              row.daysRemaining !== null && row.daysRemaining < 0 ? (
                <Badge key="status" tone="danger">
                  {copy("หมดอายุ · ห้ามเบิก", "Expired · blocked")}
                </Badge>
              ) : (
                <Badge key="status" tone="success">
                  {copy("ใช้ได้", "Usable")}
                </Badge>
              ),
            ])}
            empty={copy(
              "ไม่พบรายการตามตัวกรอง",
              "No records match these filters",
            )}
          />
        </Card>

        <OpsNote>
          {copy(
            "ตัวกรอง 7/30/90 วันรวมช่วงสั้นกว่า แต่ไม่นับล็อตหมดอายุ · จำนวนช่วงด้านล่างไม่ซ้อนกัน · ใช้ได้ถึงวันหมดอายุตามเวลาไทย",
            "7/30/90 filters are cumulative, exclude expired lots. Counts below use exclusive buckets. Usable through expiry date in Bangkok.",
          )}
        </OpsNote>
      </div>
    </div>
  );
}
