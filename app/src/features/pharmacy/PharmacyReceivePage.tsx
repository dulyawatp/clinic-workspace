import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
  itemName,
  receivablePurchaseOrders,
  supplierName,
} from "../operations/ops-model";
import {
  FilterBar,
  FilterField,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

/**
 * PHA-05 purchase-order receipt. Progress comes from the submitted-receipt
 * source lines (`poProgress`), never from the order's mutable counters, and the
 * requested delivery date is labelled as the PO's own date, not a supplier ETA.
 *
 * The receipt write has no contract in this build, so the review control is
 * disabled with that reason: the form demonstrates the designed fields, but
 * nothing is posted and no batch or ledger row is invented.
 */
export function PharmacyReceivePage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { date, money } = useOpsFormat();
  const eligible = useMemo(() => receivablePurchaseOrders(), []);
  const [params] = useSearchParams();
  const requestedPo = params.get("po");
  // A `po` drill-down must not silently fall back to another order: an
  // unreceivable id shows the not-available state instead.
  const [poId, setPoId] = useState<string>(
    requestedPo ?? eligible[0]?.po.id ?? "",
  );
  const [lineIndex, setLineIndex] = useState(0);

  const selected = eligible.find((row) => row.po.id === poId) ?? null;
  const line = selected?.po.items[lineIndex] ?? selected?.po.items[0] ?? null;
  const progressLine =
    selected?.progress.lines[lineIndex] ?? selected?.progress.lines[0] ?? null;

  const header = (title: string) => (
    <PageHeader
      breadcrumb={
        <>
          <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
          <span aria-hidden="true">/</span>
          <span>PHA-05</span>
        </>
      }
      eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
      title={title}
    />
  );

  if (!selected || !line || !progressLine) {
    return (
      <div data-surface="PHA-05" data-testid="pharmacy-receive">
        {header(copy("รับสินค้าเข้าคลัง", "Receive stock"))}
        <div className="pc-page__body">
          <SampleBanner
            label={copy("ข้อมูลตัวอย่าง", "Demo data")}
            title={copy(
              "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
              "Browser-local sample records · server contracts unverified",
            )}
          />
          <Card>
            <div className="pc-empty">
              <h2 className="pc-empty__title">
                {copy(
                  "ไม่มีใบสั่งซื้อที่รับได้สำหรับรายการนี้ ต้องเป็น PO ยืนยันแล้ว มีผู้จำหน่าย และมียอดค้างรับ ไม่รับเอกสารร่าง ปิด หรือยกเลิก",
                  "No receivable PO for this selection. Requires submitted PO, supplier and pending quantity. Draft, closed and cancelled POs cannot receive.",
                )}
              </h2>
              <p>
                <Link to={ROUTES.pharmacyPurchasing}>
                  {copy("เปิดจัดซื้อ", "Open purchasing")}
                </Link>
              </p>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  const item = opsData.itemById(line.itemId);
  const defaultQty = Math.min(4, progressLine.pending);

  return (
    <div data-surface="PHA-05" data-testid="pharmacy-receive">
      {header(copy("รับสินค้าเข้าคลัง", "Receive stock"))}
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
          title={copy("รับสินค้าตามใบสั่งซื้อ", "Purchase-order receipt")}
          hint={`${copy("วันต้องการจาก PO", "Required date from PO")}: ${date(
            selected.po.required,
          )} · ${copy("ไม่ใช่ ETA ยืนยันจากผู้ขาย", "Not supplier-confirmed ETA")}`}
        >
          <div className="ops-card-tools">
            <FilterBar>
              <FilterField label={copy("ใบสั่งซื้อ", "Purchase order")}>
                <select
                  value={poId}
                  onChange={(event) => {
                    setPoId(event.target.value);
                    setLineIndex(0);
                  }}
                >
                  {eligible.map((row) => (
                    <option key={row.po.id} value={row.po.id}>
                      {row.po.id} · {supplierName(row.po.supplierId, locale)}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField
                label={copy("รายการในใบสั่งซื้อ", "Purchase-order line")}
              >
                <select
                  value={String(lineIndex)}
                  onChange={(event) => setLineIndex(Number(event.target.value))}
                >
                  {selected.po.items.map((orderLine, index) => (
                    <option
                      key={`${orderLine.itemId}-${index}`}
                      value={index}
                    >
                      {itemName(orderLine.itemId, locale)} ·{" "}
                      {selected.progress.lines[index]?.received ?? 0}/
                      {selected.progress.lines[index]?.ordered ?? 0}
                    </option>
                  ))}
                </select>
              </FilterField>
            </FilterBar>
          </div>

          <div className="ops-form">
            <div className="ops-form__grid">
              <FilterField label={copy("ผู้จำหน่ายจาก PO", "Supplier from PO")}>
                <input
                  value={supplierName(selected.po.supplierId, locale)}
                  readOnly
                />
              </FilterField>
              <FilterField label={copy("รายการสินค้า", "Item")}>
                <input value={item ? item.name[locale] : line.itemId} readOnly />
              </FilterField>
              <FilterField
                label={copy("จำนวนรับครั้งนี้", "Quantity received now")}
              >
                <input
                  type="number"
                  min={1}
                  max={progressLine.pending}
                  defaultValue={defaultQty}
                />
              </FilterField>
              <FilterField label={copy("เลขล็อตใหม่", "New batch number")}>
                <input defaultValue="" />
              </FilterField>
              <FilterField label={copy("วันหมดอายุ", "Expiry date")}>
                <input type="date" defaultValue="" />
              </FilterField>
              <FilterField label={copy("คลังรับเข้า", "Destination warehouse")}>
                <select defaultValue={selected.po.warehouse}>
                  {opsData.warehouses.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name[locale]}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField label={copy("ต้นทุนต่อหน่วย (THB)", "Unit cost (THB)")}>
                <input type="number" defaultValue={line.rate} />
              </FilterField>
              <FilterField
                label={copy("วันที่สินค้ามาถึงจริง", "Actual arrival date")}
              >
                <input type="date" defaultValue={opsData.dates.todayKey()} />
              </FilterField>
            </div>
            <p className="pc-status-line">
              {copy("สั่ง", "Ordered")}: {progressLine.ordered} ·{" "}
              {copy("รับจากใบรับยืนยัน", "Submitted receipts")}:{" "}
              {progressLine.received} · {copy("ค้างรับ", "Pending")}:{" "}
              {progressLine.pending} {item?.uom ?? ""} ·{" "}
              {copy("ต้นทุนต่อหน่วย (THB)", "Unit cost (THB)")}:{" "}
              {money(line.rate)}
            </p>
            <div className="ops-progress">
              <progress
                max={100}
                value={selected.progress.percent}
                aria-label={copy("รับแล้ว", "Received")}
              />
              <Badge
                tone={selected.progress.percent === 100 ? "success" : "info"}
              >
                {selected.progress.percent}%
              </Badge>
              <span className="pc-note">
                {copy("แหล่งใบรับยืนยัน", "Submitted receipt sources")}:{" "}
                <span className="ops-mono">
                  {selected.progress.receiptIds.join(", ") || "—"}
                </span>
              </span>
            </div>
            <div className="ops-form__actions">
              <button
                type="button"
                className="pc-button pc-button--primary"
                disabled
                aria-describedby="receive-not-connected"
              >
                {copy("ตรวจทานและรับสินค้า", "Review & receive")}
              </button>
              <span className="pc-field__hint" id="receive-not-connected">
                {t("procedure.noTransport")}
              </span>
            </div>
          </div>
        </Card>

        <Card
          title={copy("รับสินค้าตามใบสั่งซื้อ", "Purchase-order receipt")}
          flush
        >
          <DataTable
            caption={copy("รับสินค้าตามใบสั่งซื้อ", "Purchase-order receipt")}
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
            rows={selected.progress.lines.map((row) => [
              itemName(row.itemId, locale),
              row.ordered,
              row.received,
              row.pending,
            ])}
            empty={copy(
              "ไม่พบรายการตามตัวกรอง",
              "No records match these filters",
            )}
          />
        </Card>

        <OpsNote>{t("checkout.writeUnavailable")}</OpsNote>
      </div>
    </div>
  );
}
