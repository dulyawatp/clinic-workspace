import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  Drawer,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { opsData } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";
import { itemStockByWarehouse, itemTotals, warehouseName } from "../operations/ops-model";

const GROUP_FILTERS = ["all", "stock", "service"] as const;
type GroupFilter = (typeof GROUP_FILTERS)[number];

/**
 * PHA-02 item catalogue. The detail drawer reports stock per warehouse from the
 * fixture's `stockRows` helper: physical quantity, usable quantity (expired lots
 * excluded, per the helper), and that location's configured reorder threshold —
 * never the item master's single `reorder` number.
 */
export function PharmacyItemsPage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money } = useOpsFormat();
  const [filter, setFilter] = useState<GroupFilter>("all");
  const [search, setSearch] = useState("");
  const [openItem, setOpenItem] = useState<string | null>(null);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return opsData.items.filter((item) => {
      if (filter === "stock" && !item.isStock) return false;
      if (filter === "service" && item.isStock) return false;
      if (!needle) return true;
      return `${item.code} ${item.name.th} ${item.name.en}`
        .toLowerCase()
        .includes(needle);
    });
  }, [filter, search]);

  const detail = openItem ? opsData.itemById(openItem) : null;
  const detailRows = openItem ? itemStockByWarehouse(openItem) : [];
  const totals = openItem ? itemTotals(openItem) : null;

  return (
    <div data-surface="PHA-02" data-testid="pharmacy-items">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>PHA-02</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("รายการยาและวัสดุ", "Items catalogue")}
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
          title={copy("รายการยา วัสดุ และบริการ", "Medicines, supplies & services")}
          hint={copy(
            "คงเหลือจริงรวมล็อตหมดอายุ · ใช้ได้ไม่รวมล็อตหมดอายุ · จุดสั่งซื้อเป็นค่าตัวอย่างรายสินค้าและคลัง",
            "Physical includes expired stock; usable excludes expired lots. Thresholds are explicit sample item–location settings.",
          )}
          flush
        >
          <div className="ops-card-tools">
            <FilterBar>
              <FilterField label={copy("กรองรายการ", "Filter records")}>
                <select
                  value={filter}
                  onChange={(event) =>
                    setFilter(event.target.value as GroupFilter)
                  }
                >
                  <option value="all">{copy("ทั้งหมด", "All")}</option>
                  <option value="stock">
                    {copy("สินค้าสต็อก", "Stock item")}
                  </option>
                  <option value="service">
                    {copy("สินค้าไม่เก็บสต็อก", "Non-stock item")}
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
          </div>
          <DataTable
            caption={copy(
              "รายการยา วัสดุ และบริการ",
              "Medicines, supplies & services",
            )}
            columns={[
              { key: "item", label: copy("รายการสินค้า", "Item") },
              { key: "group", label: copy("กลุ่ม / หน่วย", "Group / UOM") },
              { key: "batch", label: copy("ติดตามล็อต", "Batch tracked") },
              {
                key: "onHand",
                label: copy("ยอดคงเหลือ", "On hand"),
                align: "end",
              },
              { key: "action", label: "" },
            ]}
            rows={rows.map((item) => [
              <span key="item">
                <strong>{item.name[locale]}</strong>
                <br />
                <span className="pc-note ops-mono">{item.code}</span>
              </span>,
              `${item.group} / ${item.uom}`,
              item.batchTracked ? copy("ใช่", "Yes") : copy("ไม่", "No"),
              item.isStock ? (
                <span key="onHand">{opsData.stockOnHand(item.id)}</span>
              ) : (
                <span className="pc-note" key="onHand">
                  {copy("สินค้าไม่เก็บสต็อก", "Non-stock item")}
                </span>
              ),
              <button
                key="action"
                type="button"
                className="pc-button pc-button--secondary pc-button--sm"
                onClick={() => setOpenItem(item.id)}
              >
                {copy("รายละเอียด", "Details")}
              </button>,
            ])}
            empty={copy(
              "ไม่พบรายการตามตัวกรอง",
              "No records match these filters",
            )}
          />
        </Card>

        <OpsNote>
          {copy(
            "คงเหลือจริงรวมล็อตหมดอายุ · ใช้ได้ไม่รวมล็อตหมดอายุ · จุดสั่งซื้อเป็นค่าตัวอย่างรายสินค้าและคลัง",
            "Physical includes expired stock; usable excludes expired lots. Thresholds are explicit sample item–location settings.",
          )}
        </OpsNote>
      </div>

      {detail && totals ? (
        <Drawer
          title={`${detail.name[locale]} · ${detail.code}`}
          onClose={() => setOpenItem(null)}
          footer={
            <button
              type="button"
              className="pc-button pc-button--secondary"
              onClick={() => setOpenItem(null)}
            >
              {copy("ปิด", "Close")}
            </button>
          }
        >
          <dl className="pc-dl">
            <div>
              <dt>{copy("รหัส", "Code")}</dt>
              <dd className="ops-mono">{detail.code}</dd>
            </div>
            <div>
              <dt>UOM</dt>
              <dd>{detail.uom}</dd>
            </div>
            <div>
              <dt>{copy("ราคา", "Price")}</dt>
              <dd>{money(detail.price)}</dd>
            </div>
            <div>
              <dt>{copy("ติดตามล็อต", "Batch tracked")}</dt>
              <dd>
                {detail.batchTracked ? copy("ใช่", "Yes") : copy("ไม่", "No")}
              </dd>
            </div>
            <div>
              <dt>{copy("คงเหลือจริง / ใช้ได้", "Physical / usable")}</dt>
              <dd>
                {totals.onHand} / {totals.usable}
              </dd>
            </div>
          </dl>
          <h3 className="pc-card__title ops-drawer-title">
            {copy(
              "สินค้าและจุดสั่งซื้อตามคลัง",
              "Item–location stock & thresholds",
            )}
          </h3>
          <DataTable
            caption={copy(
              "สินค้าและจุดสั่งซื้อตามคลัง",
              "Item–location stock & thresholds",
            )}
            columns={[
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
            rows={detailRows.map((row) => [
              <span key="warehouse">
                {warehouseName(row.warehouse, locale)}
                <br />
                <span className="pc-note ops-mono">{row.warehouse}</span>
              </span>,
              `${row.onHand} / ${row.usable}`,
              row.threshold === null ? (
                <span key="threshold" className="pc-note">
                  {copy("ไม่พร้อมใช้งาน", "Unavailable")}
                </span>
              ) : (
                <span key="threshold">
                  {row.threshold}
                  {row.low ? (
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
            empty={copy("ไม่พร้อมใช้งาน", "Unavailable")}
          />
        </Drawer>
      ) : null}
    </div>
  );
}
