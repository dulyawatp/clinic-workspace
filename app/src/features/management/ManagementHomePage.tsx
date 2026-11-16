import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import {
  Badge,
  Card,
  DataTable,
  PageHeader,
  SampleBanner,
} from "../../design/components";
import { confirmedRevenueSeries } from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import { type RevenueDay, opsData, periodKeys } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { SurfaceIcon } from "../clinical/SurfaceIcon";
import { useOpsCopy } from "../operations/ops-copy";
import {
  Buckets,
  FilterBar,
  FilterField,
  MiniChart,
  OpsNote,
  SourceNote,
  useOpsFormat,
} from "../operations/ops-ui";

type Period = "today" | "week" | "month";

const PERIOD_DAYS: Record<Period, number> = { today: 1, week: 7, month: 30 };

/**
 * MGR-01 clinic overview. The eight KPI tiles are `metrics(period)` values:
 * appointment cohort by appointment date, completions by completion date,
 * invoices by posting date, collections by payment date; outstanding and stock
 * stay all-dates/current-snapshot and say so. The chart plots the derived
 * confirmed-revenue series against invoiced and collected figures, and the same
 * numbers stay readable in the table beneath it.
 */
export function ManagementHomePage() {
  const copy = useOpsCopy();
  const { locale } = useLocale();
  const { money, date } = useOpsFormat();
  const [period, setPeriod] = useState<Period>("week");

  const metrics = useMemo(() => opsData.metrics(period), [period]);
  const seriesDays = PERIOD_DAYS[period];
  const series = useMemo(
    () => confirmedRevenueSeries(seriesDays),
    [seriesDays],
  );
  const daily = useMemo(
    () =>
      periodKeys(period).map((key) => ({
        key,
        ...opsData.metrics("today", { anchor: key }),
      })),
    [period],
  );

  const funnel = [
    {
      id: "scheduled",
      th: "นัดหมาย",
      en: "Scheduled",
      value: metrics.appointmentRows.filter((row) => row.status === "scheduled")
        .length,
    },
    {
      id: "arrived",
      th: "มาถึง / รับเข้าตรวจ / รอ / กำลังตรวจ",
      en: "Arrived / checked in / waiting / with doctor",
      value: metrics.appointmentRows.filter((row) =>
        ["arrived", "checkedIn", "waiting", "withDoctor"].includes(row.status),
      ).length,
    },
    {
      id: "completed",
      th: "นัดหมายเสร็จแล้ว",
      en: "Completed appointments",
      value: metrics.appointmentRows.filter((row) => row.status === "completed")
        .length,
    },
    {
      id: "cancelled",
      th: "ยกเลิก",
      en: "Cancelled",
      value: metrics.appointmentRows.filter((row) =>
        ["cancelled", "noShow"].includes(row.status),
      ).length,
    },
  ];

  const availabilityReasons: Record<string, { th: string; en: string }> = {
    closed: { th: "คลินิกปิด", en: "Clinic closed" },
    schedule: { th: "นอกตาราง", en: "Outside schedule" },
    break: { th: "เวลาพัก", en: "Break" },
    leave: { th: "ลา", en: "Leave" },
    conflict: { th: "มีนัดแล้ว", en: "Appointment conflict" },
    room: { th: "ห้องไม่ว่าง", en: "Room unavailable" },
    invalid: { th: "ข้อมูลไม่ครบ", en: "Invalid context" },
  };
  const today = opsData.dates.todayKey();
  const availability = coreData.practitioners.map((practitioner) => {
    const result = opsData.availability({
      practitioner: practitioner.id,
      date: today,
      time: "09:00",
      minutes: 30,
    });
    const reason = result.reason ? availabilityReasons[result.reason] : null;
    return { practitioner, result, reason };
  });

  const kpis = [
    {
      key: "appointments",
      label: copy("นัดหมายทั้งหมดในช่วง", "All period appointments"),
      value: String(metrics.appointments),
      tab: "appointments",
    },
    {
      key: "checkedIn",
      label: copy("รับเข้าตรวจ", "Checked in"),
      value: String(metrics.checkedIn),
      tab: "appointments",
    },
    {
      key: "completed",
      label: copy("ตรวจเสร็จ", "Completed visits"),
      value: String(metrics.completed),
      tab: "appointments",
    },
    {
      key: "invoiced",
      label: copy("ยอดใบแจ้งหนี้", "Invoiced revenue"),
      value: money(metrics.invoiced),
      tab: "finance",
    },
    {
      key: "collected",
      label: copy("รับเงินแล้ว", "Collected payments"),
      value: money(metrics.collected),
      tab: "finance",
    },
    {
      key: "outstanding",
      label: copy("ค้างชำระ · ทุกวัน", "Outstanding · all dates"),
      value: money(metrics.outstanding),
      tab: "finance",
    },
    {
      key: "lowStock",
      label: copy("ใกล้หมด / หมด · ปัจจุบัน", "Low / out stock · current"),
      value: String(metrics.lowStock),
      tab: "inventory",
    },
    {
      key: "expiring",
      label: copy("หมดอายุใน 30 วัน · ปัจจุบัน", "Expiring in 30 days · current"),
      value: String(metrics.expiring),
      tab: "inventory",
    },
  ];

  return (
    <div data-surface="MGR-01" data-testid="management-page">
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{copy("กลับหน้าหลัก", "Back to home")}</Link>
            <span aria-hidden="true">/</span>
            <span>MGR-01</span>
          </>
        }
        eyebrow={`${copy("เดโม คลินิก", "Demo Clinic")} · ${copy("ข้อมูลตัวอย่าง", "Demo data")}`}
        title={copy("ภาพรวมคลินิก", "Clinic overview")}
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
        <FilterBar>
          <FilterField label={copy("ช่วงเวลา", "Period")}>
            <select
              value={period}
              onChange={(event) => setPeriod(event.target.value as Period)}
            >
              <option value="today">{copy("วันนี้", "Today")}</option>
              <option value="week">{copy("สัปดาห์นี้", "This week")}</option>
              <option value="month">{copy("เดือนนี้", "This month")}</option>
            </select>
          </FilterField>
        </FilterBar>

        <section className="pc-kpis" aria-label={copy("ภาพรวมคลินิก", "Clinic overview")}>
          {kpis.map((kpi) => (
            <Link
              className="pc-kpi"
              data-manager-kpi={kpi.key}
              key={kpi.key}
              to={`${ROUTES.managementReports}?tab=${kpi.tab}&metric=${kpi.key}&period=${period}`}
            >
              <span className="pc-kpi__label">{kpi.label}</span>
              <strong className="pc-kpi__value">{kpi.value}</strong>
            </Link>
          ))}
        </section>

        <Card
          title={copy(
            "เส้นทางนัดหมาย · ข้อมูลต้นทางไม่ใช่คิวโทเคน",
            "Appointment funnel · source states, not queue tokens",
          )}
          hint={copy(
            "กลุ่มนัดหมายรวมรายการยกเลิกและไม่มาตามนัด สถานะมาจากนัดหมายในช่วงที่เลือก ยอดตรวจเสร็จใน KPI มาจากวันจบการตรวจ จึงอาจต่างกัน",
            "Appointment cohort includes cancelled and no-show records. Funnel uses appointment states within selected period. Completed KPI uses visit completion date and may differ.",
          )}
        >
          <Buckets
            label={copy(
              "เส้นทางนัดหมาย · ข้อมูลต้นทางไม่ใช่คิวโทเคน",
              "Appointment funnel · source states, not queue tokens",
            )}
            entries={funnel.map((row) => ({
              key: row.id,
              label: copy(row.th, row.en),
              value: row.value,
            }))}
          />
        </Card>

        <Card
          title={copy(
            "ยอดใบแจ้งหนี้และรับเงินรายวัน",
            "Daily invoice and collection trend",
          )}
          hint={copy(
            "ยอดค้างทุกวันไม่เท่ากับใบแจ้งหนี้ลบรับเงินในช่วง · ไม่รวมเอกสารร่าง/ยกเลิก",
            "All-dates outstanding is not period invoices minus collections. Draft/cancelled financial documents excluded.",
          )}
        >
          <MiniChart
            title={copy(
              "ยอดใบแจ้งหนี้และรับเงินรายวัน",
              "Daily invoice and collection trend",
            )}
            labels={series.map((row: RevenueDay) => row.key)}
            series={[
              {
                label: copy("ยอดใบแจ้งหนี้", "Invoiced revenue"),
                tone: "ink",
                values: series.map((row) => row.invoiced),
              },
              {
                label: copy("รับเงินแล้ว", "Collected payments"),
                tone: "cyan",
                values: series.map((row) => row.collected),
              },
              {
                label: copy("รายได้ที่ยืนยันแล้ว", "Confirmed revenue"),
                tone: "muted",
                values: series.map((row) => row.confirmed),
              },
            ]}
          />
          <DataTable
            caption={copy(
              "ยอดใบแจ้งหนี้และรับเงินรายวัน",
              "Daily invoice and collection trend",
            )}
            columns={[
              { key: "date", label: copy("วันที่ตามปฏิทิน", "Calendar date") },
              {
                key: "invoiced",
                label: copy("ใบแจ้งหนี้ที่มีสิทธิ์", "Eligible invoices"),
                align: "end",
              },
              {
                key: "collected",
                label: copy("รับเงินที่จัดสรร", "Allocated collection"),
                align: "end",
              },
              {
                key: "confirmed",
                label: copy("รายได้ที่ยืนยันแล้ว", "Confirmed revenue"),
                align: "end",
              },
            ]}
            rows={daily.map((row, index) => [
              date(`${row.key}T00:00:00+07:00`),
              money(row.invoiced),
              money(row.collected),
              money(series[index]?.confirmed ?? 0),
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <Card
          title={copy(
            "ความพร้อมแพทย์จากตารางตัวอย่าง",
            "Doctor availability from sample schedules",
          )}
          flush
        >
          <DataTable
            caption={copy(
              "ความพร้อมแพทย์จากตารางตัวอย่าง",
              "Doctor availability from sample schedules",
            )}
            columns={[
              { key: "practitioner", label: copy("แพทย์", "Practitioner") },
              { key: "slot", label: copy("เวลาที่ตรวจ", "Checked slot") },
              { key: "availability", label: copy("ความพร้อม", "Availability") },
              { key: "source", label: copy("แหล่งข้อมูล", "Source") },
            ]}
            rows={availability.map((row) => [
              row.practitioner.name[locale],
              <span key="slot">
                {date(`${today}T00:00:00+07:00`)} 09:00–09:30
              </span>,
              row.result.available ? (
                <Badge key="availability" tone="success">
                  {copy("ว่างในตัวอย่าง", "Available in sample")}
                </Badge>
              ) : (
                <Badge key="availability" tone="warning">
                  {row.reason ? copy(row.reason.th, row.reason.en) : copy("ไม่ทราบ", "Unknown")}
                </Badge>
              ),
              <span key="source" className="ops-mono">
                {row.result.source}
              </span>,
            ])}
            empty={copy("ไม่พบรายการตามตัวกรอง", "No records match these filters")}
          />
        </Card>

        <section className="pc-tilegrid" aria-label={copy("รายงานบริหาร", "Management reports")}>
          {[
            {
              icon: "chart",
              label: copy("ระเบียนการเงิน", "Finance sources"),
              to: `${ROUTES.managementReports}?tab=finance&period=${period}`,
            },
            {
              icon: "layers",
              label: copy("ภาพรวมคลังปัจจุบัน", "Current inventory sources"),
              to: `${ROUTES.managementReports}?tab=inventory&period=${period}`,
            },
            {
              icon: "truck",
              label: copy("ใบสั่งซื้อและใบรับ", "PO & receipt sources"),
              to: `${ROUTES.managementReports}?tab=purchasing&period=${period}`,
            },
          ].map((tile) => (
            <Link className="pc-tile" to={tile.to} key={tile.to}>
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

        <OpsNote>
          {copy(
            "จำนวนนัดหมายรวมรายการยกเลิกและไม่มาตามนัด อิงวันนัด ตรวจเสร็จอิงวันจบการตรวจ ใบแจ้งหนี้อิงวันโพสต์ รับเงินอิงวันรับ ยอดค้างทุกวันและคลังเป็นภาพรวมปัจจุบัน",
            "Appointment count includes cancelled and no-show records, using appointment business date; completions use completed visit date; invoices use posting date; collections use payment date. Outstanding is all dates; inventory is a current snapshot.",
          )}
        </OpsNote>
      </div>
    </div>
  );
}
