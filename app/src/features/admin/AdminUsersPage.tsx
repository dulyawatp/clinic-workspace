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
import { permissionMatrix } from "../../fixtures/data-authored";
import { staff } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import { OpsNote, SourceNote } from "../operations/ops-ui";

const RIGHTS = [
  "read",
  "write",
  "create",
  "submit",
  "cancel",
  "amend",
] as const;

/**
 * ADM-02 staff and role bundles plus the permission matrix. The matrix is a
 * presentation fixture (`verified: false`) shown read-only: this surface has no
 * permission write contract, so no control pretends to change a right. Identity
 * notes keep the difference between a role-only demo login and a linked
 * practitioner explicit.
 */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();

  const noPractitioner = staff.filter((member) => member.practitioner === null);

  return (
    <div data-surface="ADM-02" data-testid="admin-users">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>ADM-02</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("พนักงานและสิทธิ์", "Staff & roles")}
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
            "บัญชีตัวอย่างและสิทธิ์งาน",
            "Sample staff accounts & role bundles",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "บัญชีตัวอย่างและสิทธิ์งาน",
              "Sample staff accounts & role bundles",
            )}
            columns={[
              { key: "staff", label: copy("พนักงาน", "Staff") },
              {
                key: "account",
                label: copy("บัญชี / บทบาท", "Account / roles"),
              },
              { key: "practitioner", label: copy("แพทย์ที่เชื่อม", "Practitioner") },
              { key: "locale", label: copy("ภาษา", "Locale") },
              { key: "status", label: copy("สถานะ", "Status") },
            ]}
            rows={staff.map((member) => [
              <span key="staff">
                {member.name[locale]}
                <br />
                <span className="pc-note ops-mono">{member.id}</span>
              </span>,
              <span key="account">
                <span className="ops-mono">{member.user}</span>
                <br />
                <span className="pc-note">
                  {t(`role.${member.role.toLowerCase()}.short`)} ·{" "}
                  {member.frappeRoles.join(", ")}
                </span>
              </span>,
              member.practitioner ?? "—",
              member.lang === "th" ? "ไทย" : "English",
              member.active ? (
                <Badge key="status" tone="success">
                  {t("status.active")}
                </Badge>
              ) : (
                <Badge key="status" tone="neutral">
                  {t("status.inactive")}
                </Badge>
              ),
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <OpsNote>
          {copy(
            "ตัวตนตัวอย่างมีเฉพาะบทบาท เซสชันไม่มีข้อมูลเชื่อมแพทย์ จึงยังไม่ยืนยันการเชื่อมตัวตน",
            "Demo identities are role-only: the session carries no practitioner link, so identity mapping is unverified",
          )}
        </OpsNote>
        <OpsNote>
          {copy(
            "บัญชีแพทย์ต้องเชื่อมข้อมูลแพทย์ บัญชีที่ไม่ใช่คลินิกไม่มีการเชื่อมโดยเจตนา",
            "Doctor accounts require a practitioner link; non-clinical accounts intentionally have none",
          )}
          : {noPractitioner.map((member) => member.id).join(", ")}
        </OpsNote>

        <Card
          title={copy("ขอบเขตสิทธิ์ตัวอย่าง", "Sample role scope")}
          hint={t("gate.readOnly")}
          flush
        >
          <DataTable
            caption={copy("ขอบเขตสิทธิ์ตัวอย่าง", "Sample role scope")}
            columns={[
              // DocType and permission names stay in Frappe vocabulary: they
              // are the installed site's technical permission labels.
              { key: "doctype", label: "DocType" },
              { key: "role", label: "Role" },
              ...RIGHTS.map((right) => ({
                key: right,
                label: right,
                align: "end" as const,
              })),
            ]}
            rows={permissionMatrix.map((row) => [
              row.doctype,
              row.role,
              ...RIGHTS.map((right) => (
                <span key={right}>
                  {row[right] ? copy("ใช่", "Yes") : copy("ไม่", "No")}
                </span>
              )),
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <OpsNote>
          {copy("ขอบเขตสิทธิ์ตัวอย่าง", "Sample role scope")} ·{" "}
          {copy("ยังไม่ยืนยัน", "Unverified")} · {t("gate.readOnly")}
        </OpsNote>
      </div>
    </div>
  );
}
