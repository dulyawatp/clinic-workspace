import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
  type PoStatusFilter,
  itemName,
  poProgressOf,
  poStatusKey,
  purchaseOrdersFor,
  supplierName,
  warehouseName,
} from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";
import {
  PurchaseOrderDraftForm,
  PurchaseOrderSubmitForm,
} from "./purchase-write";

const STATUSES: ReadonlyArray<{
  id: PoStatusFilter;
  th: string;
  en: string;
}> = [
  { id: "all", th: "ทั้งหมด", en: "All" },
  { id: "open", th: "ใบสั่งซื้อที่ยังเปิด", en: "Open purchase orders" },
  { id: "draft", th: "ฉบับร่าง", en: "Draft" },
  { id: "submitted", th: "ยืนยันแล้ว", en: "Submitted" },
  { id: "partlyReceived", th: "รับบางส่วน", en: "Partly received" },
  { id: "closed", th: "ปิดแล้ว", en: "Closed" },
];

const PO_TONES = {
  draft: "neutral",
  submitted: "info",
  partlyReceived: "warning",
  closed: "success",
  cancelled: "danger",
} as const;

/**
 * PHA-07 purchasing. Receipt progress is the computed `poProgress` view over
 * submitted receipt sources; the PO's requested date is explicitly not a
 * supplier-confirmed ETA. Creating a draft PO has no contract in this build, so
 * the designed control is rendered disabled with that reason.
 */
export function PharmacyPurchasingPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { date } = useOpsFormat();
  const [params] = useSearchParams();
  const initialWarehouse = params.get("warehouse") ?? "all";
  const initialStatus = params.get("state");
  const initialTab = params.get("tab") === "suppliers" ? "suppliers" : "orders";
  const [tab, setTab] = useState(initialTab);
  const [warehouse, setWarehouse] = useState(
    opsData.warehouses.some((row) => row.id === initialWarehouse)
      ? initialWarehouse
      : "all",
  );
  const [status, setStatus] = useState<PoStatusFilter>(
    STATUSES.some((row) => row.id === initialStatus)
      ? (initialStatus as PoStatusFilter)
      : "all",
  );
  const [search, setSearch] = useState("");
  const [openPo, setOpenPo] = useState<string | null>(null);
  const [openSupplier, setOpenSupplier] = useState<string | null>(null);

  const orders = useMemo(
    () => purchaseOrdersFor(warehouse, status, search),
    [warehouse, status, search],
  );
  const detail = openPo ? opsData.purchaseOrders.find((po) => po.id === openPo) : null;
  const detailProgress = detail ? poProgressOf(detail.id) : null;
  const supplier = openSupplier
    ? opsData.suppliers.find((row) => row.id === openSupplier)
    : null;

  return (
    <div data-surface="PHA-07" data-testid="pharmacy-purchasing">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>PHA-07</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("จัดซื้อและผู้จำหน่าย", "Purchasing & suppliers")}
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
          label={copy("จัดซื้อและผู้จำหน่าย", "Purchasing & suppliers")}
          active={tab}
          onChange={setTab}
          tabs={[
            { id: "orders", label: copy("ใบสั่งซื้อ", "Purchase orders") },
            { id: "suppliers", label: copy("ผู้จำหน่าย", "Suppliers") },
          ]}
        />

        {tab === "suppliers" ? (
          <Card
            title={copy("จัดซื้อและติดตามค้างรับ", "Purchasing & pending receipts")}
            flush
          >
            <DataTable
              caption={copy("ผู้จำหน่าย", "Suppliers")}
              columns={[
                { key: "supplier", label: copy("ผู้จำหน่าย", "Supplier") },
                { key: "contact", label: copy("ติดต่อ", "Contact") },
                { key: "phone", label: copy("โทร", "Phone") },
                { key: "terms", label: copy("เครดิต", "Terms") },
                { key: "action", label: "" },
              ]}
              rows={opsData.suppliers.map((row) => [
                row.name[locale],
                row.contact,
                row.phone,
                row.terms,
                <button
                  key="action"
                  type="button"
                  className="pc-button pc-button--secondary pc-button--sm"
                  onClick={() => setOpenSupplier(row.id)}
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
        ) : (
          <Card
            title={copy(
              "จัดซื้อและติดตามค้างรับ",
              "Purchasing & pending receipts",
            )}
            hint={copy(
              "ความคืบหน้าจากใบรับยืนยันที่ไม่ซ้ำ · วันต้องการจาก PO ไม่ใช่ ETA ผู้ขาย · ไม่มีประวัติการแก้ PO จึงไม่สร้างแนวโน้มย้อนหลัง",
              "Progress uses distinct submitted receipt sources. PO requested date is not supplier ETA. No PO revision history, so no invented historical trend.",
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
                <FilterField label={copy("สถานะ", "Status")}>
                  <select
                    value={status}
                    onChange={(event) =>
                      setStatus(event.target.value as PoStatusFilter)
                    }
                  >
                    {STATUSES.map((row) => (
                      <option key={row.id} value={row.id}>
                        {copy(row.th, row.en)}
                      </option>
                    ))}
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
              <div className="ops-form__actions">
                <button
                  type="button"
                  className="pc-button pc-button--secondary"
                  disabled
                  aria-describedby="po-not-connected"
                >
                  {copy("สร้างร่างใบสั่งซื้อ", "New draft purchase order")}
                </button>
                <span className="pc-field__hint" id="po-not-connected">
                  {t("checkout.writeUnavailable")}
                </span>
              </div>
            </div>
            <DataTable
              caption={copy(
                "จัดซื้อและติดตามค้างรับ",
                "Purchasing & pending receipts",
              )}
              columns={[
                {
                  key: "po",
                  label: copy("ใบสั่งซื้อ / คลัง", "PO / warehouse"),
                },
                { key: "supplier", label: copy("ผู้จำหน่าย", "Supplier") },
                {
                  key: "required",
                  label: copy("วันต้องการจาก PO", "Required date from PO"),
                },
                {
                  key: "progress",
                  label: copy(
                    "รับแล้ว / สั่ง / ค้าง",
                    "Received / ordered / pending",
                  ),
                },
                {
                  key: "status",
                  label: copy("สถานะ / แหล่งรับ", "Status / receipt sources"),
                },
                { key: "action", label: "" },
              ]}
              rows={orders.map((po) => {
                const progress = poProgressOf(po.id);
                return [
                  <span key="po">
                    {po.id}
                    <br />
                    <span className="pc-note">
                      {po.warehouse
                        ? warehouseName(po.warehouse, locale)
                        : copy("ไม่ระบุคลัง", "Warehouse not recorded")}
                    </span>
                  </span>,
                  supplierName(po.supplierId, locale),
                  <span key="required">
                    {date(po.required)}
                    <br />
                    <span className="pc-note">
                      {copy(
                        "ไม่ใช่ ETA ยืนยันจากผู้ขาย",
                        "Not supplier-confirmed ETA",
                      )}
                    </span>
                  </span>,
                  <span key="progress">
                    {progress?.received ?? 0} / {progress?.ordered ?? 0} /{" "}
                    {progress?.pending ?? 0}
                    <br />
                    <span className="ops-progress">
                      <progress
                        max={100}
                        value={progress?.percent ?? 0}
                        aria-label={copy("รับแล้ว", "Received")}
                      />
                      <span className="pc-note">{progress?.percent ?? 0}%</span>
                    </span>
                  </span>,
                  <span key="status">
                    <Badge tone={PO_TONES[po.status]}>
                      {t(poStatusKey(po.status))}
                    </Badge>
                    <br />
                    <span className="pc-note ops-mono">
                      {progress?.receiptIds.join(", ") ||
                        copy("ไม่มีใบรับที่ยืนยัน", "No submitted receipts")}
                    </span>
                  </span>,
                  <button
                    key="action"
                    type="button"
                    className="pc-button pc-button--secondary pc-button--sm"
                    onClick={() => setOpenPo(po.id)}
                  >
                    {copy("รายละเอียด", "Details")}
                  </button>,
                ];
              })}
              empty={copy(
                "ไม่พบรายการตามตัวกรอง",
                "No records match these filters",
              )}
            />
          </Card>
        )}

        <OpsNote>
          {copy(
            "ความคืบหน้าจากใบรับยืนยันที่ไม่ซ้ำ · วันต้องการจาก PO ไม่ใช่ ETA ผู้ขาย · ไม่มีประวัติการแก้ PO จึงไม่สร้างแนวโน้มย้อนหลัง",
            "Progress uses distinct submitted receipt sources. PO requested date is not supplier ETA. No PO revision history, so no invented historical trend.",
          )}
        </OpsNote>

        {tab === "orders" ? (
          <>
            <PurchaseOrderDraftForm />
            <PurchaseOrderSubmitForm />
          </>
        ) : null}
      </div>

      {detail && detailProgress ? (
        <Drawer
          title={detail.id}
          onClose={() => setOpenPo(null)}
          footer={
            <button
              type="button"
              className="pc-button pc-button--secondary"
              onClick={() => setOpenPo(null)}
            >
              {copy("ปิด", "Close")}
            </button>
          }
        >
          <dl className="pc-dl">
            <div>
              <dt>{copy("ผู้จำหน่าย", "Supplier")}</dt>
              <dd>{supplierName(detail.supplierId, locale)}</dd>
            </div>
            <div>
              <dt>{copy("วันต้องการจาก PO", "Required date from PO")}</dt>
              <dd>
                {date(detail.required)} ·{" "}
                {copy("ไม่ใช่ ETA ยืนยันจากผู้ขาย", "Not supplier-confirmed ETA")}
              </dd>
            </div>
            <div>
              <dt>{copy("แหล่งใบรับยืนยัน", "Submitted receipt sources")}</dt>
              <dd className="ops-mono">
                {detailProgress.receiptIds.join(", ") ||
                  copy("ไม่มีใบรับที่ยืนยัน", "No submitted receipts")}
              </dd>
            </div>
          </dl>
          <DataTable
            caption={copy("ใบสั่งซื้อ", "Purchase orders")}
            columns={[
              { key: "item", label: copy("รายการสินค้า", "Item") },
              { key: "ordered", label: copy("สั่ง", "Ordered"), align: "end" },
              {
                key: "received",
                label: copy("รับจากใบรับยืนยัน", "Submitted receipts"),
                align: "end",
              },
              {
                key: "pending",
                label: copy("ค้างรับ", "Pending"),
                align: "end",
              },
            ]}
            rows={detailProgress.lines.map((row) => [
              itemName(row.itemId, locale),
              row.ordered,
              row.received,
              row.pending,
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
          {detailProgress.pending > 0 ? (
            <p className="ops-form__actions">
              <Link
                className="pc-button pc-button--primary"
                to={`${ROUTES.pharmacyReceive}?po=${encodeURIComponent(detail.id)}`}
              >
                {copy("รับเข้าคลัง", "Receive stock")}
              </Link>
            </p>
          ) : null}
        </Drawer>
      ) : null}

      {supplier ? (
        <Drawer
          title={supplier.name[locale]}
          onClose={() => setOpenSupplier(null)}
          footer={
            <button
              type="button"
              className="pc-button pc-button--secondary"
              onClick={() => setOpenSupplier(null)}
            >
              {copy("ปิด", "Close")}
            </button>
          }
        >
          <dl className="pc-dl">
            <div>
              <dt>{copy("ติดต่อ", "Contact")}</dt>
              <dd>{supplier.contact}</dd>
            </div>
            <div>
              <dt>{copy("โทร", "Phone")}</dt>
              <dd>{supplier.phone}</dd>
            </div>
            <div>
              <dt>{copy("เครดิต", "Terms")}</dt>
              <dd>{supplier.terms}</dd>
            </div>
          </dl>
          <DataTable
            caption={copy("ใบสั่งซื้อ", "Purchase orders")}
            columns={[
              { key: "po", label: copy("ใบสั่งซื้อ", "Purchase order") },
              { key: "status", label: copy("สถานะ", "Status") },
              {
                key: "pending",
                label: copy("ค้างรับ", "Pending"),
                align: "end",
              },
            ]}
            rows={opsData.purchaseOrders
              .filter((po) => po.supplierId === supplier.id)
              .map((po) => [
                po.id,
                <Badge key="status" tone={PO_TONES[po.status]}>
                  {t(poStatusKey(po.status))}
                </Badge>,
                poProgressOf(po.id)?.pending ?? 0,
              ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Drawer>
      ) : null}
    </div>
  );
}
