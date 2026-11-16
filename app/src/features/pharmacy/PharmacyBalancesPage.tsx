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
import { itemName, warehouseName } from "../operations/ops-model";
import {
  CsvButton,
  FilterBar,
  FilterField,
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const STATES = ["all", "stocked", "low", "out"] as const;
type StockState = (typeof STATES)[number];

/**
 * PHA-04 balances and the immutable ledger. Balances are per item–location from
 * the fixture helper (physical includes expired lots, usable excludes them,
 * thresholds are explicit or unavailable — never an implicit zero). The ledger
 * tab reads the core sample ledger in its stored chronological order.
 */
export function PharmacyBalancesPage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { dateTime } = useOpsFormat();
  const [params] = useSearchParams();
  const initialWarehouse = params.get("warehouse") ?? "all";
  const initialTab = params.get("tab") === "ledger" ? "ledger" : "balances";
  const initialStatus = params.get("state");
  const [tab, setTab] = useState(initialTab);
  const [warehouse, setWarehouse] = useState(
    opsData.warehouses.some((row) => row.id === initialWarehouse)
      ? initialWarehouse
      : "all",
  );
  const [state, setState] = useState<StockState>(
    STATES.includes(initialStatus as StockState)
      ? (initialStatus as StockState)
      : "all",
  );
  const [search, setSearch] = useState("");

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return opsData.stockRows(warehouse === "all" ? undefined : warehouse).filter((row) => {
      if (state === "stocked" && row.onHand <= 0) return false;
      if (state === "low" && !row.low) return false;
      if (state === "out" && !row.out) return false;
      if (!needle) return true;
      return `${row.itemId} ${itemName(row.itemId, "th")} ${itemName(row.itemId, "en")}`
        .toLowerCase()
        .includes(needle);
    });
  }, [warehouse, state, search]);

  const ledger = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return opsData.movements.filter((movement) => {
      if (warehouse !== "all" && movement.warehouse !== warehouse) return false;
      if (!needle) return true;
      return `${movement.ref} ${movement.batchId}`.toLowerCase().includes(needle);
    });
  }, [warehouse, search]);

  const csv = [
    ["SAMPLE", "Item", "Warehouse", "Physical", "Usable", "Threshold"],
    ...rows.map((row) => [
      "SAMPLE",
      row.itemId,
      row.warehouse,
      row.onHand,
      row.usable,
      row.reorderLevel ?? "Unavailable",
    ]),
  ];

  return (
    <div data-surface="PHA-04" data-testid="pharmacy-balances">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>PHA-04</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ยอดคงเหลือและบัญชีคลัง", "Balances & ledger")}
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
        <OpsTabs
          label={copy("ยอดคงเหลือและบัญชีคลัง", "Balances & ledger")}
          active={tab}
          onChange={setTab}
          tabs={[
            { id: "balances", label: copy("ยอดคงเหลือ", "Balances") },
            { id: "ledger", label: copy("บัญชีคลัง", "Immutable ledger") },
          ]}
        />

        <Card
          title={copy(
            "สินค้าและจุดสั่งซื้อตามคลัง",
            "Item–location stock & thresholds",
          )}
          hint={copy(
            "คงเหลือจริงรวมล็อตหมดอายุ · ใช้ได้ไม่รวมล็อตหมดอายุ · จุดสั่งซื้อเป็นค่าตัวอย่างรายสินค้าและคลัง",
            "Physical includes expired stock; usable excludes expired lots. Thresholds are explicit sample item–location settings.",
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
              <FilterField label={copy("สถานะคลัง", "Stock state")}>
                <select
                  value={state}
                  onChange={(event) =>
                    setState(event.target.value as StockState)
                  }
                >
                  <option value="all">{copy("ทั้งหมด", "All")}</option>
                  <option value="stocked">
                    {copy("คงเหลือจริงเป็นบวก", "Positive physical stock")}
                  </option>
                  <option value="low">{copy("ใกล้หมด", "Low stock")}</option>
                  <option value="out">
                    {copy("หมดคลังที่ใช้ได้", "Out of usable stock")}
                  </option>
                </select>
              </FilterField>
              <FilterField label={copy("ค้นหารายการ", "Search item")}>
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </FilterField>
            </FilterBar>
            {tab === "balances" ? (
              <CsvButton
                filename="sample-balances.csv"
                rows={csv}
                label={copy("ส่งออกข้อมูลที่กรอง", "Export filtered sample")}
              />
            ) : null}
          </div>

          {tab === "ledger" ? (
            <DataTable
              caption={copy("บัญชีคลัง", "Immutable ledger")}
              columns={[
                {
                  key: "at",
                  label: copy("เวลา / เอกสาร", "Time / document"),
                },
                {
                  key: "item",
                  label: copy("รายการ / ล็อต", "Item / batch"),
                },
                { key: "warehouse", label: copy("คลัง", "Warehouse") },
                {
                  key: "qty",
                  label: copy("เข้า / ออก", "In / out"),
                  align: "end",
                },
              ]}
              rows={ledger.map((movement) => [
                <span key="at">
                  {dateTime(movement.at)}
                  <br />
                  <span className="pc-note ops-mono">{movement.ref}</span>
                </span>,
                <span key="item">
                  {itemName(movement.itemId, locale)}
                  <br />
                  <span className="pc-note ops-mono">{movement.batchId}</span>
                </span>,
                warehouseName(movement.warehouse, locale),
                <span key="qty">
                  {movement.qty > 0 ? "+" : ""}
                  {movement.qty}
                </span>,
              ])}
              empty={copy(
                "ไม่พบรายการตามตัวกรอง",
                "No records match these filters",
              )}
            />
          ) : (
            <DataTable
              caption={copy(
                "สินค้าและจุดสั่งซื้อตามคลัง",
                "Item–location stock & thresholds",
              )}
              columns={[
                { key: "item", label: copy("รายการสินค้า", "Item") },
                { key: "warehouse", label: copy("คลัง", "Warehouse") },
                {
                  key: "balance",
                  label: copy("คงเหลือจริง / ใช้ได้", "Physical / usable"),
                  align: "end",
                },
                {
                  key: "threshold",
                  label: copy("จุดสั่งซื้อ", "Reorder threshold"),
                  align: "end",
                },
              ]}
              rows={rows.map((row) => [
                <span key="item">
                  {itemName(row.itemId, locale)}
                  <br />
                  <span className="pc-note ops-mono">{row.itemId}</span>
                </span>,
                warehouseName(row.warehouse, locale),
                `${row.onHand} / ${row.usable}`,
                row.reorderLevel === null ? (
                  <span key="threshold" className="pc-note">
                    {copy("ไม่พร้อมใช้งาน", "Unavailable")}
                  </span>
                ) : (
                  <span key="threshold">
                    {row.reorderLevel}
                    {row.out ? (
                      <>
                        {" "}
                        <Badge tone="danger">
                          {copy("หมดคลังที่ใช้ได้", "Out of usable stock")}
                        </Badge>
                      </>
                    ) : row.low ? (
                      <>
                        {" "}
                        <Badge tone="warning">
                          {copy("ใกล้หมด", "Low stock")}
                        </Badge>
                      </>
                    ) : null}
                  </span>
                ),
              ])}
              empty={copy(
                "ไม่พบรายการตามตัวกรอง",
                "No records match these filters",
              )}
            />
          )}
        </Card>

        <OpsNote>
          {copy(
            "คงเหลือจริงรวมล็อตหมดอายุ · ใช้ได้ไม่รวมล็อตหมดอายุ · จุดสั่งซื้อเป็นค่าตัวอย่างรายสินค้าและคลัง",
            "Physical includes expired stock; usable excludes expired lots. Thresholds are explicit sample item–location settings.",
          )}
        </OpsNote>
      </div>
    </div>
  );
}
