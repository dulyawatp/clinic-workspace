import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { useLocale } from "../../i18n/LocaleProvider";
import { SurfaceIcon } from "../clinical/SurfaceIcon";
import { useOpsCopy } from "../operations/ops-copy";
import {
  CHECK_STATUS_COPY,
  GATE_EVIDENCE,
  checkStatusTone,
  systemChecks,
} from "../operations/system-gates";
import { OpsNote, SourceNote } from "../operations/ops-ui";

/**
 * ADM-01 setup overview. The readiness gate list renders each sample check with
 * its own status — unverified, extension required, or decision required — so the
 * page never flattens the eight rows into one "Unverified" chip.
 */
export function AdminHomePage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const checks = systemChecks();

  return (
    <div data-surface="ADM-01" data-testid="admin-home">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>ADM-01</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ตั้งค่าระบบ", "Setup overview")}
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

        <section className="pc-tilegrid" aria-label={copy("ตั้งค่าระบบ", "Setup overview")}>
          {[
            {
              icon: "shield",
              label: copy("พนักงานและสิทธิ์", "Staff & roles"),
              to: ROUTES.adminUsers,
            },
            {
              icon: "calendar",
              label: copy("ตารางแพทย์", "Doctor schedules"),
              to: ROUTES.adminClinical,
            },
            {
              icon: "clipboard",
              label: copy("บริการและห้อง", "Services & rooms"),
              to: ROUTES.adminClinical,
            },
            {
              icon: "box",
              label: copy("คลังและราคา", "Inventory & prices"),
              to: ROUTES.adminSettings,
            },
            {
              icon: "globe",
              label: copy("ค่าเริ่มต้นสองภาษา", "Bilingual defaults"),
              to: ROUTES.adminSettings,
            },
            {
              icon: "activity",
              label: copy("ตรวจสอบและความพร้อม", "Audit & readiness"),
              to: ROUTES.adminSystem,
            },
          ].map((tile) => (
            <Link className="pc-tile" to={tile.to} key={tile.label}>
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

        <Card
          title={copy(
            "ประตูตรวจสอบก่อนใช้งานจริง",
            "Production readiness gates",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "ประตูตรวจสอบก่อนใช้งานจริง",
              "Production readiness gates",
            )}
            columns={[
              { key: "gate", label: copy("หัวข้อตรวจ", "Verification gate") },
              { key: "status", label: copy("สถานะ", "Status") },
              { key: "required", label: copy("สิ่งที่ยังต้องทำ", "Required evidence") },
            ]}
            rows={checks.map((check) => [
              check.name[locale],
              <Badge key="status" tone={checkStatusTone(check.status)}>
                {copy(...CHECK_STATUS_COPY[check.status])}
              </Badge>,
              check.note[locale],
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <OpsNote>
          {copy(
            "ไม่มีการตรวจสุขภาพเซิร์ฟเวอร์จริงในต้นแบบ จึงไม่แสดงสถานะระบบปกติปลอม",
            "No actual server health checks in prototype. No fabricated healthy status.",
          )}
        </OpsNote>
        <OpsNote>{copy(...GATE_EVIDENCE)}</OpsNote>
      </div>
    </div>
  );
}
