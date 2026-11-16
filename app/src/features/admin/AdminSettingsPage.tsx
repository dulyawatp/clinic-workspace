import { useState } from "react";
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
import {
  cashierShift,
  stockThresholdPolicy,
  stockThresholdSettings,
} from "../../fixtures/data-authored";
import { opsData } from "../../fixtures/data-ops";
import { formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  itemName,
  warehouseName,
} from "../operations/ops-model";
import {
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";
import { AdminMasterWriteForm } from "./admin-write";

const TABS = [
  { id: "thresholds", th: "จุดสั่งซื้อ", en: "Reorder threshold" },
  { id: "warehouses", th: "คลังและรายการ", en: "Warehouses & items" },
  { id: "pricing", th: "ราคา", en: "Pricing" },
  { id: "accounts", th: "เงินสด/บัตร", en: "Cash/card accounts" },
  { id: "language", th: "สองภาษา", en: "Bilingual defaults" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * ADM-04 inventory, pricing and settings. Read-only: the sample thresholds are
 * per item–location (the retail shelf is intentionally unconfigured), and the
 * cash/card account codes are sample values that do not verify a chart of
 * accounts. No settings write is offered because no contract exists.
 */
export function AdminSettingsPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { date } = useOpsFormat();
  const [params] = useSearchParams();
  const requested = params.get("tab");
  const [tab, setTab] = useState<TabId>(
    TABS.some((row) => row.id === requested) ? (requested as TabId) : "thresholds",
  );

  return (
    <div data-surface="ADM-04" data-testid="admin-settings">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>ADM-04</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("คลัง ราคา และตั้งค่า", "Inventory, pricing & settings")}
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
          label={copy("ตั้งค่าตัวอย่างในเครื่อง", "Local sample settings")}
          active={tab}
          onChange={(next) => setTab(next as TabId)}
          tabs={TABS.map((row) => ({ id: row.id, label: copy(row.th, row.en) }))}
        />

        <Card
          title={copy("ตั้งค่าตัวอย่างในเครื่อง", "Local sample settings")}
          hint={t("gate.readOnly")}
          flush
        >
          {tab === "thresholds" ? (
            <DataTable
              caption={copy("จุดสั่งซื้อ", "Reorder threshold")}
              columns={[
                { key: "item", label: copy("รายการสินค้า", "Item") },
                { key: "warehouse", label: copy("คลัง", "Warehouse") },
                {
                  key: "level",
                  label: copy("จุดสั่งซื้อ", "Reorder threshold"),
                  align: "end",
                },
                { key: "updated", label: copy("วันที่ตามปฏิทิน", "Calendar date") },
              ]}
              rows={stockThresholdSettings.map((row) => [
                <span key="item">
                  {itemName(row.itemId, locale)}
                  <br />
                  <span className="pc-note ops-mono">{row.itemId}</span>
                </span>,
                warehouseName(row.warehouse, locale),
                row.reorderLevel,
                <span key="updated">
                  {date(row.updatedAt)}
                  <br />
                  <span className="pc-note ops-mono">{row.updatedBy}</span>
                </span>,
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "warehouses" ? (
            <>
              <DataTable
                caption={copy("คลังและรายการ", "Warehouses & items")}
                columns={[
                  { key: "warehouse", label: copy("คลัง", "Warehouse") },
                  { key: "id", label: copy("รหัส", "Code") },
                ]}
                rows={opsData.warehouses.map((row) => [
                  row.name[locale],
                  <span key="id" className="ops-mono">
                    {row.id}
                  </span>,
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
              <AdminMasterWriteForm masterType="uom" />
              <AdminMasterWriteForm masterType="supplier_group" />
              <AdminMasterWriteForm masterType="warehouse" />
              <AdminMasterWriteForm masterType="supplier" />
            </>
          ) : null}

          {tab === "pricing" ? (
            <>
              <DataTable
                caption={copy("ราคา", "Pricing")}
                columns={[
                  { key: "item", label: copy("รายการสินค้า", "Item") },
                  { key: "uom", label: copy("หน่วย", "UOM") },
                  { key: "price", label: copy("ราคา", "Price"), align: "end" },
                ]}
                rows={opsData.items.map((row) => [
                  row.name[locale],
                  row.uom,
                  formatMoney(row.price, locale),
                ])}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
              <AdminMasterWriteForm masterType="price_list" />
              <AdminMasterWriteForm masterType="item" />
            </>
          ) : null}

          {tab === "accounts" ? (
            <>
              <DataTable
                caption={copy("เงินสด/บัตร", "Cash/card accounts")}
                columns={[
                  { key: "kind", label: copy("วิธีชำระ", "Payment mode") },
                  { key: "account", label: copy("รหัส", "Code") },
                ]}
                rows={[
                  [
                    copy("เงินสด", "Cash"),
                    <span key="cash" className="ops-mono">
                      {cashierShift.cashAccount}
                    </span>,
                  ],
                  [
                    copy("บัตรเครดิต", "Credit Card"),
                    <span key="card" className="ops-mono">
                      {cashierShift.cardAccount}
                    </span>,
                  ],
                ]}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
              <OpsNote>
                {copy(
                  "รหัสบัญชีตัวอย่างไม่ใช่การยืนยันผังบัญชีจริง ต้องตรวจการตั้งค่าก่อนรับเงินจริง",
                  "Sample codes do not verify actual chart of accounts. Verify mappings before real payments.",
                )}
              </OpsNote>
              <AdminMasterWriteForm masterType="mode_of_payment" />
            </>
          ) : null}

          {tab === "language" ? (
            <>
              <DataTable
                caption={copy("สองภาษา", "Bilingual defaults")}
                columns={[
                  { key: "setting", label: copy("สองภาษา", "Bilingual defaults") },
                  { key: "value", label: copy("ภาษา", "Locale") },
                ]}
                rows={[
                  [
                    copy("ภาษาเริ่มต้นคลินิก", "Clinic default locale"),
                    <Badge key="clinic" tone="neutral">
                      ไทย
                    </Badge>,
                  ],
                  [
                    copy("ภาษาของใบเสร็จ", "Receipt output language"),
                    <Badge key="print" tone="neutral">
                      ไทย
                    </Badge>,
                  ],
                ]}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
              <OpsNote>
                {copy(
                  "ค่ารายบุคคลมีผลเหนือค่าเริ่มต้น ไม่มีการซิงก์ข้ามอุปกรณ์ในต้นแบบ",
                  "Per-user preference overrides default. No cross-device sync in prototype.",
                )}
              </OpsNote>
            </>
          ) : null}
        </Card>

        <OpsNote>
          {stockThresholdPolicy.note[locale]} · {t("gate.readOnly")}
        </OpsNote>
      </div>
    </div>
  );
}
