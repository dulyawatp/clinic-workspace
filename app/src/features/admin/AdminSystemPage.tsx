import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { audit } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  CHECK_STATUS_COPY,
  GATE_EVIDENCE,
  V3_GATES,
  checkStatusTone,
  systemChecks,
} from "../operations/system-gates";
import {
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const TABS = [
  { id: "overview", th: "ตรวจความพร้อม", en: "Verification status" },
  { id: "audit", th: "ประวัติรายการ", en: "Audit trail" },
  { id: "gates", th: "ประตูตรวจสัญญา", en: "Contract gate" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * ADM-05 audit and readiness. Statuses stay as the fixture states them
 * (unverified / extension required / decision required), the audit trail lists
 * account ids because the sample data has no display names, and the 18 BC3
 * gates are all unverified: prototype behaviour cannot pass an installed-site
 * contract gate.
 */
export function AdminSystemPage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { dateTime } = useOpsFormat();
  const [params] = useSearchParams();
  const requested = params.get("tab");
  const [tab, setTab] = useState<TabId>(
    TABS.some((row) => row.id === requested) ? (requested as TabId) : "overview",
  );

  return (
    <div data-surface="ADM-05" data-testid="admin-system">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>ADM-05</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ตรวจสอบระบบและการเชื่อมต่อ", "Audit & readiness")}
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
          label={copy("ตรวจสอบระบบและการเชื่อมต่อ", "Audit & readiness")}
          active={tab}
          onChange={(next) => setTab(next as TabId)}
          tabs={TABS.map((row) => ({ id: row.id, label: copy(row.th, row.en) }))}
        />

        {tab === "overview" ? (
          <Card
            title={copy("ไม่มีการเชื่อมต่อเซิร์ฟเวอร์จริง", "No live backend connection")}
            flush
          >
            <DataTable
              caption={copy(
                "ไม่มีการเชื่อมต่อเซิร์ฟเวอร์จริง",
                "No live backend connection",
              )}
              columns={[
                { key: "gate", label: copy("หัวข้อตรวจ", "Verification gate") },
                { key: "status", label: copy("สถานะ", "Status") },
                {
                  key: "required",
                  label: copy("สิ่งที่ยังต้องทำ", "Required evidence"),
                },
              ]}
              rows={systemChecks().map((check) => [
                check.name[locale],
                <Badge key="status" tone={checkStatusTone(check.status)}>
                  {copy(...CHECK_STATUS_COPY[check.status])}
                </Badge>,
                check.note[locale],
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          </Card>
        ) : null}

        {tab === "audit" ? (
          <Card
            title={copy(
              "บันทึกตัวอย่าง ปกปิดข้อมูลละเอียดอ่อน",
              "Sample audit — sensitive content redacted",
            )}
            flush
          >
            <DataTable
              caption={copy(
                "บันทึกตัวอย่าง ปกปิดข้อมูลละเอียดอ่อน",
                "Sample audit — sensitive content redacted",
              )}
              columns={[
                { key: "at", label: copy("เวลา / เอกสาร", "Time / document") },
                { key: "actor", label: copy("ผู้ใช้", "Actor") },
                { key: "action", label: copy("การกระทำ", "Action") },
                { key: "entity", label: copy("อ้างอิง", "Reference") },
                { key: "note", label: copy("บันทึก", "Note") },
              ]}
              rows={audit.map((row) => [
                dateTime(row.at),
                <span key="actor" className="ops-mono">
                  {row.actor}
                </span>,
                row.action[locale],
                <span key="entity" className="ops-mono">
                  {row.entity}
                </span>,
                row.note[locale],
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
            <OpsNote>
              {copy(
                "ผู้ใช้แสดงเป็นรหัสบัญชี ยังไม่ยืนยันชื่อในข้อมูลตัวอย่าง",
                "Actors are account IDs; names are not verified in sample data",
              )}
            </OpsNote>
          </Card>
        ) : null}

        {tab === "gates" ? (
          <Card title={copy("ประตูตรวจสัญญา", "Contract gate")} flush>
            <DataTable
              caption={copy("ประตูตรวจสัญญา", "Contract gate")}
              columns={[
                { key: "gate", label: copy("ประตูตรวจสัญญา", "Contract gate") },
                { key: "status", label: copy("สถานะ", "Status") },
                {
                  key: "required",
                  label: copy("หลักฐานที่ต้องมี", "Evidence required"),
                },
              ]}
              rows={V3_GATES.map((gate) => [
                `${gate.id} · ${copy(gate.th, gate.en)}`,
                <Badge key="status" tone="warning">
                  {copy("ยังไม่ยืนยัน", "Unverified")}
                </Badge>,
                copy(...GATE_EVIDENCE),
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          </Card>
        ) : null}

        <OpsNote>
          {copy(
            "เวอร์ชัน แบ็กอัป การกู้คืน การเข้ารหัส และสิทธิ์เซิร์ฟเวอร์ยังไม่ได้ตรวจ ไม่มีการแสดงผ่านหรือออนไลน์โดยไม่มีหลักฐาน",
            "Versions, backups, restores, encryption and server permissions not inspected. No healthy/live claims without evidence.",
          )}
        </OpsNote>
      </div>
    </div>
  );
}
