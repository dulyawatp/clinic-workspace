import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Badge, DataTable, SampleBanner } from "../../design/components";
import type { Locale } from "../../i18n";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "../operations/ops-copy";
import {
  CHECK_STATUS_COPY,
  GATE_EVIDENCE,
  V3_GATES,
  checkStatusTone,
  systemChecks,
} from "../operations/system-gates";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { StatusChip } from "../../ui/StatusChip";
import { RoutedControl, WriteGate } from "../../ui/WriteGate";
import {
  PRINT_TEMPLATES,
  PrintPreview,
  type PrintTemplate,
} from "../print/PrintPreview";

export function SystemStatesPage() {
  const { t } = useTranslation();
  const copy = useOpsCopy();
  const { locale } = useLocale();
  // Deliberate sample default, not derived from UI locale or stored preference.
  const [outputLocale, setOutputLocale] = useState<Locale>("th");
  const [template, setTemplate] = useState<PrintTemplate>("receipt");
  const id = useId();
  return (
    <>
      <PageHeader title={t("surface.APP-04")} />
      <div className="shell-page-body system-page">
        <section className="system-intro" aria-labelledby={`${id}-title`}>
          <div>
            <h2 id={`${id}-title`}>{t("system.title")}</h2>
            <p>{t("system.demoNote")}</p>
          </div>
          <Link className="system-read-link" to={ROUTES.home}>
            {t("action.goHome")}
          </Link>
        </section>
        <div className="system-states">
          <section
            className="ui-state system-tile"
            aria-labelledby={`${id}-populated`}
          >
            <h2 id={`${id}-populated`}>{t("system.populated")}</h2>
            <p>{t("system.populatedBody")}</p>
            <div className="system-sample-row">
              <span>{t("system.sampleItem")}</span>
              <StatusChip tone="info" label={t("system.sampleTag")} />
            </div>
          </section>
          <StatePanel
            kind="empty"
            title={t("system.empty")}
            detail={t("system.emptyBody")}
          />
          <StatePanel
            kind="loading"
            title={t("system.loading")}
            detail={t("system.loadingBody")}
          />
          <section
            className="ui-state system-tile system-validation"
            aria-labelledby={`${id}-validation`}
          >
            <h2 id={`${id}-validation`}>{t("system.validation")}</h2>
            <label htmlFor={`${id}-field`}>{t("system.sampleField")}</label>
            <input
              id={`${id}-field`}
              value=""
              readOnly
              aria-invalid="true"
              aria-describedby={`${id}-field-error`}
            />
            <p id={`${id}-field-error`}>{t("system.validationBody")}</p>
          </section>
          <StatePanel
            kind="error"
            title={t("system.error")}
            detail={t("system.errorBody")}
          />
          <section
            className="ui-state system-tile"
            aria-labelledby={`${id}-success`}
          >
            <h2 id={`${id}-success`}>{t("system.success")}</h2>
            <p>{t("system.successBody")}</p>
            <StatusChip tone="success" label={t("system.sampleTag")} />
          </section>
          <StatePanel
            kind="permission-denied"
            title={t("system.denied")}
            detail={t("system.deniedBody")}
          />
          <section
            className="ui-state system-tile"
            aria-labelledby={`${id}-unsaved`}
          >
            <h2 id={`${id}-unsaved`}>{t("system.unsaved")}</h2>
            <p>{t("system.unsavedBody")}</p>
            <StatusChip tone="warning" label={t("system.sampleTag")} />
          </section>
        </div>
        <section className="system-gate-demo" aria-labelledby={`${id}-gate`}>
          <h2 id={`${id}-gate`}>{t("system.gateTitle")}</h2>
          <p>{t("system.gateBody")}</p>
          <WriteGate capability="write.patient.update">
            <RoutedControl to={`${ROUTES.system}#system-preview`}>
              {t("system.gateLink")}
            </RoutedControl>
          </WriteGate>
        </section>
        <section
          id="system-preview"
          className="system-preview-card"
          aria-labelledby={`${id}-preview`}
        >
          <h2 id={`${id}-preview`}>{t("system.previewTitle")}</h2>
          <p>{t("system.previewBody")}</p>
          <div className="system-preview-controls">
            <div>
              <label htmlFor={`${id}-template`}>{t("print.documents")}</label>
              <select
                id={`${id}-template`}
                value={template}
                onChange={(event) =>
                  setTemplate(event.target.value as PrintTemplate)
                }
              >
                {PRINT_TEMPLATES.map((item) => (
                  <option key={item} value={item}>
                    {t(`print.doc.${item}`)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={`${id}-locale`}>
                {t("print.outputLanguage")}
              </label>
              <select
                id={`${id}-locale`}
                value={outputLocale}
                onChange={(event) =>
                  setOutputLocale(event.target.value as Locale)
                }
              >
                <option value="th" lang="th">
                  ไทย
                </option>
                <option value="en" lang="en">
                  English
                </option>
              </select>
            </div>
          </div>
          <PrintPreview template={template} locale={outputLocale} />
        </section>

        <SampleBanner
          label={t("app.demoTag")}
          title={copy(
            "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
            "Browser-local sample records · server contracts unverified",
          )}
        >
          <p className="pc-note">
            {copy(
              "ไม่มีการเชื่อมต่อเซิร์ฟเวอร์จริง",
              "No live backend connection",
            )}
          </p>
        </SampleBanner>

        <section aria-labelledby={`${id}-checks`}>
          <h2 className="pc-card__title" id={`${id}-checks`}>
            {copy("ตรวจความพร้อม", "Verification status")}
          </h2>
          <DataTable
            caption={copy("ตรวจความพร้อม", "Verification status")}
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
        </section>

        <section aria-labelledby={`${id}-gates`}>
          <h2 className="pc-card__title" id={`${id}-gates`}>
            {copy("ประตูตรวจสัญญา", "Contract gate")}
          </h2>
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
        </section>
        <p className="pc-note">
          {copy(
            "เวอร์ชัน แบ็กอัป การกู้คืน การเข้ารหัส และสิทธิ์เซิร์ฟเวอร์ยังไม่ได้ตรวจ ไม่มีการแสดงผ่านหรือออนไลน์โดยไม่มีหลักฐาน",
            "Versions, backups, restores, encryption and server permissions not inspected. No healthy/live claims without evidence.",
          )}
        </p>
      </div>
    </>
  );
}
