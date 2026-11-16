/**
 * Readiness vocabulary shared by APP-04, ADM-01 and ADM-05.
 *
 * The prototype lists 18 contract gates (BC3-01…18) and an eight-row system
 * check. Each row keeps its own status: `unverified`, `extension` (a model has
 * to be built) or `decision` (a product decision is pending). Nothing here
 * promotes a row to "verified" — the sample data cannot prove an installed-site
 * contract.
 */
import type { BadgeTone } from "../../design/components";
import { opsData } from "../../fixtures/data-ops";

export type CheckStatus = "unverified" | "extension" | "decision";

export const CHECK_STATUS_COPY: Record<CheckStatus, readonly [string, string]> = {
  unverified: ["ยังไม่ยืนยัน", "Unverified"],
  extension: ["ต้องมีส่วนขยาย", "Extension required"],
  decision: ["ต้องตัดสินใจ", "Decision required"],
};

export function checkStatusTone(status: CheckStatus): BadgeTone {
  return status === "unverified" ? "warning" : status === "extension" ? "info" : "neutral";
}

/** Every sample system check, in fixture order. */
export function systemChecks() {
  return opsData.systemChecks;
}

export const V3_GATES: ReadonlyArray<{
  readonly id: string;
  readonly th: string;
  readonly en: string;
}> = [
  { id: "BC3-01", th: "รับเข้าตรวจ", en: "Check-in" },
  {
    id: "BC3-02",
    th: "สถานะและเวลาคิว",
    en: "Queue states and timestamps",
  },
  {
    id: "BC3-03",
    th: "คำขอนัดหมาย P1",
    en: "Appointment requests · P1",
  },
  {
    id: "BC3-04",
    th: "เวลาว่าง พัก ลา ห้อง บริษัท",
    en: "Availability, breaks, leave, room, company",
  },
  {
    id: "BC3-05",
    th: "สัญญาณชีพล่าสุดและสิทธิ์",
    en: "Latest vitals and permissions",
  },
  {
    id: "BC3-06",
    th: "ความเชื่อมโยงนัดหมายและการตรวจ",
    en: "Appointment and encounter links",
  },
  {
    id: "BC3-07",
    th: "วงจรแล็บและผู้ตรวจผล",
    en: "Lab lifecycle and reviewer",
  },
  {
    id: "BC3-08",
    th: "ไฟล์ส่วนตัวและการลองใหม่",
    en: "Private uploads and retry",
  },
  {
    id: "BC3-09",
    th: "คลังที่ใช้ได้และจุดสั่งซื้อ",
    en: "Usable stock and reorder policy",
  },
  {
    id: "BC3-10",
    th: "หมดอายุและการนับไม่ซ้ำ",
    en: "Expiry and exclusive counting",
  },
  {
    id: "BC3-11",
    th: "ร่าง PO และสิทธิ์ผู้จำหน่าย",
    en: "Draft PO, supplier and permissions",
  },
  {
    id: "BC3-12",
    th: "ใบรับยืนยันและการยกเลิก",
    en: "Submitted receipts and cancellation",
  },
  {
    id: "BC3-13",
    th: "กระทบยอดและตัดจำหน่าย",
    en: "Reconciliation and write-off",
  },
  {
    id: "BC3-14",
    th: "รับเงินจัดสรรและยกเลิก",
    en: "Allocated payments and cancellation",
  },
  {
    id: "BC3-15",
    th: "แหล่งรายการใบแจ้งหนี้",
    en: "Invoice-item source mapping",
  },
  {
    id: "BC3-16",
    th: "สิทธิ์แจ้งเตือนและรหัสต้นทาง",
    en: "Notification permissions and source IDs",
  },
  {
    id: "BC3-17",
    th: "สองภาษา ร่าง และภาษาพิมพ์",
    en: "Locales, drafts and print language",
  },
  {
    id: "BC3-18",
    th: "เซสชัน CSRF ข้อมูลล้าสมัย และบันทึก",
    en: "Session, CSRF, stale records and audit",
  },
];

export const GATE_EVIDENCE: readonly [string, string] = [
  "ต้องตรวจ metadata วิธีทำงาน สิทธิ์ และผลทดสอบจากไซต์ติดตั้งจริง ต้นแบบนี้ไม่ผ่านแทน",
  "Installed-site metadata, methods, permissions and test evidence required. Prototype behavior cannot pass this gate.",
];
