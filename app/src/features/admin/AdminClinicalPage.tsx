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
  labProviderMasters,
  practitionerMasters,
} from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import { opsData } from "../../fixtures/data-ops";
import { formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  OpsNote,
  OpsTabs,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

const TABS = [
  { id: "types", th: "ประเภทนัด", en: "Appointment type" },
  { id: "schedules", th: "ตารางแพทย์", en: "Schedules" },
  { id: "rooms", th: "ห้อง", en: "Rooms" },
  { id: "procedures", th: "หัตถการ", en: "Procedures" },
  { id: "labs", th: "การตรวจ", en: "Test" },
  { id: "practitioners", th: "แพทย์", en: "Practitioners" },
  { id: "providers", th: "ผู้ให้บริการแล็บ", en: "Lab provider" },
  { id: "hours", th: "เวลาทำการ", en: "Working hours" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * ADM-03 clinical masters. Every tab is a read-only sample master; the working
 * hours tab shows the same `hours()` source the availability helper validates
 * against, without an editor — schedule and hours writes have no contract here.
 */
export function AdminClinicalPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { date } = useOpsFormat();
  const [params] = useSearchParams();
  const requested = params.get("tab");
  const [tab, setTab] = useState<TabId>(
    TABS.some((row) => row.id === requested) ? (requested as TabId) : "types",
  );
  const hours = opsData.hours();

  return (
    <div data-surface="ADM-03" data-testid="admin-clinical">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>ADM-03</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ข้อมูลหลักทางคลินิก", "Clinical masters")}
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
          label={copy("ข้อมูลหลักทางคลินิก", "Clinical masters")}
          active={tab}
          onChange={(next) => setTab(next as TabId)}
          tabs={TABS.map((row) => ({ id: row.id, label: copy(row.th, row.en) }))}
        />

        <Card
          title={copy("ข้อมูลหลักคลินิกตัวอย่าง", "Sample clinical masters")}
          flush
        >
          {tab === "types" ? (
            <DataTable
              caption={copy("ประเภทนัด", "Appointment type")}
              columns={[
                { key: "type", label: copy("ประเภทนัด", "Appointment type") },
                {
                  key: "duration",
                  label: copy("ระยะเวลา", "Duration"),
                  align: "end",
                },
                { key: "price", label: copy("ราคา", "Price"), align: "end" },
              ]}
              rows={coreData.apptTypes.map((row) => [
                t(row.key),
                `${row.minutes} ${copy("นาที", "Minutes")}`,
                formatMoney(row.price, locale),
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "schedules" ? (
            <DataTable
              caption={copy("ตารางแพทย์", "Schedules")}
              columns={[
                { key: "doctor", label: copy("แพทย์", "Doctor") },
                { key: "day", label: copy("วัน", "Weekday") },
                { key: "time", label: copy("เวลา", "Time") },
                { key: "room", label: copy("ห้อง", "Room") },
              ]}
              rows={opsData.schedules.map((row) => [
                coreData.practitionerById(row.practitioner).name[locale],
                t(`day.${row.day}`),
                `${row.start}–${row.end}`,
                coreData.roomById(row.room)?.name[locale] ?? row.room,
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "rooms" ? (
            <DataTable
              caption={copy("ห้อง", "Treatment room")}
              columns={[
                { key: "room", label: copy("ห้อง", "Treatment room") },
                { key: "id", label: copy("รหัส", "Code") },
              ]}
              rows={coreData.rooms.map((row) => [row.name[locale], row.id])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "procedures" ? (
            <DataTable
              caption={copy("หัตถการ", "Procedure")}
              columns={[
                { key: "procedure", label: copy("หัตถการ", "Procedure") },
                { key: "minutes", label: copy("นาที", "Minutes"), align: "end" },
                { key: "price", label: copy("ราคา", "Price"), align: "end" },
                { key: "supplies", label: copy("เวชภัณฑ์", "Supplies") },
              ]}
              rows={opsData.procedureTemplates.map((row) => [
                row.name[locale],
                row.minutes,
                formatMoney(row.price, locale),
                row.consumables
                  .map((itemId) => opsData.itemById(itemId)?.name[locale] ?? itemId)
                  .join(", "),
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "labs" ? (
            <DataTable
              caption={copy("การตรวจ", "Test")}
              columns={[
                { key: "test", label: copy("การตรวจ", "Test") },
                { key: "sample", label: copy("ตัวอย่าง", "Sample") },
              ]}
              rows={opsData.labTemplates.map((row) => [
                row.name[locale],
                row.sampleKind[locale],
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "practitioners" ? (
            <DataTable
              caption={copy("แพทย์", "Practitioner")}
              columns={[
                { key: "practitioner", label: copy("แพทย์", "Practitioner") },
                {
                  key: "registration",
                  label: copy("เลขทะเบียน", "Registration No."),
                },
                // Healthcare Practitioner designation: Frappe field label.
                { key: "designation", label: "Designation" },
                { key: "department", label: copy("แผนก", "Department") },
                {
                  key: "units",
                  label: copy("หน่วยบริการ", "Service units"),
                },
              ]}
              rows={practitionerMasters.map((row) => [
                <span key="practitioner">
                  {row.name[locale]}
                  <br />
                  <span className="pc-note ops-mono">{row.id}</span>
                </span>,
                <span key="registration" className="ops-mono">
                  {row.registrationNo}
                </span>,
                row.designation[locale],
                row.department[locale],
                row.serviceUnits.join(", "),
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "providers" ? (
            <DataTable
              caption={copy("ผู้ให้บริการแล็บ", "Lab provider")}
              columns={[
                { key: "provider", label: copy("ผู้ให้บริการแล็บ", "Lab provider") },
                { key: "contact", label: copy("ติดต่อ", "Contact") },
                { key: "phone", label: copy("โทร", "Phone") },
                { key: "prefix", label: copy("รหัส", "Code") },
                { key: "status", label: copy("สถานะ", "Status") },
              ]}
              rows={labProviderMasters.map((row) => [
                row.name[locale],
                row.contact,
                row.phone,
                <span key="prefix" className="ops-mono">
                  {row.externalRefPrefix}
                </span>,
                <span key="status">
                  {row.active ? (
                    <Badge tone="success">{copy("ใช่", "Yes")}</Badge>
                  ) : (
                    <Badge tone="neutral">{copy("ไม่", "No")}</Badge>
                  )}{" "}
                  <Badge tone="warning">
                    {copy("สัญญาตัวอย่างยังไม่ยืนยัน", "Sample contract unverified")}
                  </Badge>
                </span>,
              ])}
              empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
            />
          ) : null}

          {tab === "hours" ? (
            <div className="ops-card-tools">
              <p className="pc-status-line">
                {copy("เขตเวลา", "Timezone")}: <strong>{hours.timezone}</strong>
              </p>
              <DataTable
                caption={copy(
                  "เวลาทำการ พัก และลา",
                  "Working hours, breaks & leave",
                )}
                columns={[
                  { key: "kind", label: copy("เวลาทำการ", "Working hours") },
                  { key: "scope", label: copy("แพทย์", "Practitioner") },
                  { key: "day", label: copy("วัน", "Weekday") },
                  { key: "time", label: copy("เวลา", "Time") },
                ]}
                rows={[
                  ...hours.clinic.map((row) => [
                    copy("เวลาคลินิก", "Clinic hours"),
                    copy("ทั้งหมด", "All"),
                    row.day,
                    `${row.start}–${row.end}`,
                  ]),
                  ...hours.breaks.map((row) => [
                    copy("เวลาพัก", "Break"),
                    row.practitioner,
                    row.day,
                    `${row.start}–${row.end}`,
                  ]),
                  ...hours.leave.map((row) => [
                    copy("วันลา / ข้อยกเว้น", "Leave / exceptions"),
                    row.practitioner,
                    date(`${row.date}T00:00:00+07:00`),
                    `${row.start}–${row.end}`,
                  ]),
                ]}
                empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
              />
            </div>
          ) : null}
        </Card>

        <OpsNote>
          {copy(
            "ตารางนี้ยังไม่ยืนยันการว่างกับเซิร์ฟเวอร์ ห้ามใช้ห้องรากเป็นห้องรักษาจริง",
            "Schedules not server-verified availability. Root service unit must not be used as actual treatment room.",
          )}
        </OpsNote>
        <OpsNote>{copy("ข้อมูลหลักคลินิกตัวอย่าง", "Sample clinical masters")} · {copy("ยังไม่ยืนยัน", "Unverified")}</OpsNote>
      </div>
    </div>
  );
}
