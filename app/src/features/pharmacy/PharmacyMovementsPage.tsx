import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
import { useOpsRoles } from "../operations/ops-role";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  itemName,
  ledgerView,
  warehouseName,
} from "../operations/ops-model";
import {
  CsvButton,
  FilterField,
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

type MovementTab = "transfer" | "usage" | "reconcile";

interface SessionRow {
  readonly id: string;
  readonly tab: MovementTab;
  readonly batchId: string;
  readonly documentRef: string;
  readonly qty: number;
  readonly target: string | null;
  readonly requestOnly: boolean;
  readonly at: string;
}

const TABS: ReadonlyArray<{ id: MovementTab; th: string; en: string }> = [
  { id: "transfer", th: "โอนย้าย", en: "Transfer" },
  { id: "usage", th: "เบิกใช้จริง", en: "Actual usage" },
  {
    id: "reconcile",
    th: "คำขอกระทบยอด (จำกัดสิทธิ์)",
    en: "Reconciliation request (restricted)",
  },
];

const REASONS: Record<MovementTab, ReadonlyArray<{ th: string; en: string }>> = {
  transfer: [
    { th: "เติมคลัง", en: "Replenishment" },
    { th: "ย้ายจุดเก็บ", en: "Relocation" },
  ],
  usage: [
    { th: "ใช้ตามหัตถการ", en: "Procedure usage" },
    { th: "ใช้เวชภัณฑ์", en: "Supply usage" },
  ],
  reconcile: [
    { th: "ผลต่างตรวจนับ", en: "Count discrepancy" },
    { th: "หมดอายุ", en: "Expired" },
    { th: "เสียหาย", en: "Damaged" },
  ],
};

/**
 * PHA-06 stock operations. Transfer and usage are local sample transactions:
 * confirming one records a row in this screen only, with no ledger or balance
 * change. A reconciliation request is restricted to the administrator role (as
 * in the design), and even then stores a request only.
 *
 * The ledger below merges the core sample ledger with the authored transfer
 * pair and reconciliation request rows; the core rows themselves are untouched
 * and stay chronological (oldest first).
 */
export function PharmacyMovementsPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { dateTime } = useOpsFormat();
  const roles = useOpsRoles();
  const [tab, setTab] = useState<MovementTab>("transfer");
  const [batchId, setBatchId] = useState("");
  const [target, setTarget] = useState("");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [documentRef, setDocumentRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [recorded, setRecorded] = useState<SessionRow[]>([]);

  const batches = useMemo(() => {
    const rows = opsData.expiryRows(undefined, "all");
    return tab === "reconcile" ? rows : rows.filter((row) => row.usableQty > 0);
  }, [tab]);

  const batch = batchId ? opsData.batchById(batchId) : null;
  const sourceMovement = batch
    ? opsData.movements.find(
        (movement) => movement.batchId === batch.id && movement.qty > 0,
      )
    : null;
  const isAdmin = roles.includes("ADM");

  const merged = useMemo(() => ledgerView(), []);

  const submit = () => {
    setError(null);
    const invalid = copy(
      "ตรวจจำนวน เหตุผล เอกสารต้นทาง คลังที่ต่างกัน และวันหมดอายุ ไม่อนุญาตสต็อกติดลบ",
      "Check quantity, reason, source document, distinct warehouses and expiry. Negative stock prohibited.",
    );
    const amount = Number(qty);
    if (
      !batch ||
      !sourceMovement ||
      !documentRef.trim() ||
      !reason ||
      !Number.isFinite(amount) ||
      amount < 0 ||
      (tab !== "reconcile" && amount === 0)
    ) {
      setError(invalid);
      return;
    }
    if (tab === "reconcile") {
      if (!isAdmin) {
        setError(
          copy(
            "ต้องมีสิทธิ์กระทบยอดแยก ใช้ผู้ดูแลตัวอย่าง",
            "Separate reconciliation privilege required; use demo admin",
          ),
        );
        return;
      }
    } else {
      if (!roles.includes("PHA")) {
        setError(copy("เฉพาะคลังรับสินค้า", "Inventory role required"));
        return;
      }
      if (
        amount > batch.qty ||
        (opsData.expiryDays(batch.expiry) ?? 0) < 0 ||
        (tab === "transfer" && (!target || target === batch.warehouse))
      ) {
        setError(invalid);
        return;
      }
    }
    setRecorded((rows) => [
      ...rows,
      {
        id: `SESSION-${rows.length + 1}`,
        tab,
        batchId: batch.id,
        documentRef: documentRef.trim(),
        qty: amount,
        target: tab === "transfer" ? target : null,
        requestOnly: tab === "reconcile",
        at: new Date().toISOString(),
      },
    ]);
    setQty("");
    setDocumentRef("");
  };

  const csv = [
    ["SAMPLE", "Document", "Item", "Batch", "Warehouse", "Quantity", "Target"],
    ...merged.map((row) => [
      "SAMPLE",
      row.documentRef ?? row.ref,
      row.itemId,
      row.batchId,
      row.warehouse,
      row.qty ?? `${row.counted ?? ""} (request)`,
      row.targetWarehouse ?? "",
    ]),
  ];

  return (
    <div data-surface="PHA-06" data-testid="pharmacy-movements">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>PHA-06</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("โอนย้ายและเบิกใช้", "Stock operations")}
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
          label={copy("โอนย้ายและเบิกใช้", "Stock operations")}
          active={tab}
          onChange={(next) => {
            setTab(next as MovementTab);
            setBatchId("");
            setReason("");
            setError(null);
          }}
          tabs={TABS.map((row) => ({ id: row.id, label: copy(row.th, row.en) }))}
        />

        <Card
          title={copy(
            "ตรวจรายการและผลกระทบก่อนยืนยัน",
            "Review movement & stock effect",
          )}
        >
          <div className="ops-form">
            <div className="ops-form__grid">
              <FilterField
                label={copy("ล็อต / คลังต้นทาง", "Batch / source warehouse")}
              >
                <select
                  value={batchId}
                  onChange={(event) => setBatchId(event.target.value)}
                >
                  <option value="">
                    {copy("ยังไม่ระบุ", "Not selected")}
                  </option>
                  {batches.map((row) => (
                    <option key={row.id} value={row.id}>
                      {itemName(row.itemId, locale)} · {row.batchId} ·{" "}
                      {row.warehouse} · {row.onHand}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField label={copy("คลังปลายทาง", "Target warehouse")}>
                <select
                  value={target}
                  disabled={tab !== "transfer"}
                  onChange={(event) => setTarget(event.target.value)}
                >
                  <option value="">
                    {copy("ยังไม่ระบุ", "Not selected")}
                  </option>
                  {opsData.warehouses.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name[locale]}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField label={copy("จำนวน", "Quantity")}>
                <input
                  type="number"
                  min={0}
                  value={qty}
                  onChange={(event) => setQty(event.target.value)}
                />
              </FilterField>
              <FilterField label={copy("เหตุผลที่อนุญาต", "Controlled reason")}>
                <select
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                >
                  <option value="">
                    {copy("ยังไม่ระบุ", "Not selected")}
                  </option>
                  {REASONS[tab].map((row) => (
                    <option key={row.en} value={row.en}>
                      {copy(row.th, row.en)}
                    </option>
                  ))}
                </select>
              </FilterField>
              <FilterField
                label={copy(
                  "เอกสารอ้างอิง / เลขหัตถการ",
                  "Document / procedure reference",
                )}
              >
                <input
                  value={documentRef}
                  onChange={(event) => setDocumentRef(event.target.value)}
                />
              </FilterField>
              <FilterField
                label={copy(
                  "ธุรกรรมต้นทางของล็อต",
                  "Batch source transaction",
                )}
              >
                <input value={sourceMovement?.ref ?? ""} readOnly />
              </FilterField>
            </div>

            <div id="movement-error" role="alert" aria-live="polite">
              {error ? <p className="pc-error">{error}</p> : null}
            </div>

            <div className="ops-form__actions">
              <button
                type="button"
                className={
                  tab === "reconcile"
                    ? "pc-button pc-button--secondary"
                    : "pc-button pc-button--primary"
                }
                onClick={submit}
              >
                {tab === "reconcile"
                  ? copy("เก็บคำขอ ไม่เปลี่ยนยอดคลัง", "Request stored; stock unchanged")
                  : copy("ตรวจสอบและยืนยัน", "Review & confirm")}
              </button>
              {tab === "reconcile" && !isAdmin ? (
                <span className="pc-field__hint">
                  {copy(
                    "ต้องมีสิทธิ์กระทบยอดแยก ใช้ผู้ดูแลตัวอย่าง",
                    "Separate reconciliation privilege required; use demo admin",
                  )}
                </span>
              ) : null}
            </div>

            {recorded.length > 0 ? (
              <div role="status" className="ops-note">
                <p>
                  {copy(
                    "บันทึกธุรกรรมตัวอย่างแล้ว",
                    "Sample transaction recorded",
                  )}
                </p>
                <ul className="ops-session-list">
                  {recorded.map((row) => (
                    <li key={row.id}>
                      <span className="ops-mono">{row.documentRef}</span> ·{" "}
                      {row.batchId} · {row.qty} ·{" "}
                      {row.requestOnly
                        ? copy(
                            "เก็บคำขอ ไม่เปลี่ยนยอดคลัง",
                            "Request stored; stock unchanged",
                          )
                        : copy(
                            "บันทึกธุรกรรมตัวอย่างแล้ว",
                            "Sample transaction recorded",
                          )}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Card>

        <Card
          title={copy("บัญชีคลัง", "Immutable ledger")}
          hint={copy(
            "ลำดับธุรกรรมต้นทาง ไม่ใช่ยอดคงเหลือหรือสถานะย้อนหลังที่สร้างขึ้น",
            "Source transaction timeline, not reconstructed historical balances or document states.",
          )}
          flush
        >
          <div className="ops-card-tools">
            <CsvButton
              filename="sample-stock-ledger.csv"
              rows={csv}
              label={copy("ส่งออกข้อมูลที่กรอง", "Export filtered sample")}
            />
          </div>
          <DataTable
            caption={copy("บัญชีคลัง", "Immutable ledger")}
            columns={[
              { key: "at", label: copy("เวลา / เอกสาร", "Time / document") },
              { key: "kind", label: copy("สถานะ", "Status") },
              { key: "item", label: copy("รายการ / ล็อต", "Item / batch") },
              { key: "warehouse", label: copy("คลัง", "Warehouse") },
              {
                key: "qty",
                label: copy("เข้า / ออก", "In / out"),
                align: "end",
              },
            ]}
            rows={merged.map((row) => [
              <span key="at">
                {dateTime(row.at)}
                <br />
                <span className="pc-note ops-mono">
                  {row.documentRef ?? row.ref}
                </span>
              </span>,
              <Badge
                key="kind"
                tone={
                  row.kind === "reconciliation"
                    ? "warning"
                    : row.kind === "transfer"
                      ? "info"
                      : row.kind === "receipt"
                        ? "success"
                        : "neutral"
                }
              >
                {row.kind === "reconciliation"
                  ? copy(
                      "กระทบยอดและตัดจำหน่าย",
                      "Reconciliation and write-off",
                    )
                  : row.kind === "transfer"
                    ? copy("โอนย้าย", "Transfer")
                    : row.kind === "receipt"
                      ? copy("รับเข้าคลัง", "Receive stock")
                      : copy("เบิกใช้จริง", "Actual usage")}
              </Badge>,
              <span key="item">
                {itemName(row.itemId, locale)}
                <br />
                <span className="pc-note ops-mono">{row.batchId}</span>
              </span>,
              warehouseName(row.warehouse, locale),
              row.qty === null ? (
                <span key="qty" className="pc-note">
                  {copy("เก็บคำขอ ไม่เปลี่ยนยอดคลัง", "Request stored; stock unchanged")}
                </span>
              ) : (
                <span key="qty">
                  {row.qty > 0 ? "+" : ""}
                  {row.qty}
                </span>
              ),
            ])}
            empty={copy(
              "ไม่พบรายการตามตัวกรอง",
              "No records match these filters",
            )}
          />
        </Card>

        <OpsNote>{t("procedure.noTransport")}</OpsNote>
        <OpsNote>
          {copy(
            "ล็อตหมดอายุเบิกหรือโอนตามปกติไม่ได้ การกระทบยอด/ตัดจำหน่ายเก็บเป็นคำขอเท่านั้น ไม่แก้จำนวนหรือบัญชีคลัง ต้องใช้เอกสารและสิทธิ์เซิร์ฟเวอร์ที่ตรวจแล้ว",
            "Expired lots cannot be issued or transferred normally. Reconciliation/write-off stores a request only, never edits balances or ledger. Requires verified server document and authorization.",
          )}
        </OpsNote>
      </div>
    </div>
  );
}
