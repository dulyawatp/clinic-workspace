import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Badge, Card } from "../../design/components";
import { nativeLabSample } from "../../fixtures/data-clinical";
import { useLocale } from "../../i18n/LocaleProvider";
import {
  type LabRequestView,
  labRequestsForPatient,
  labStatusKey,
  labStatusTone,
  practitionerLabel,
} from "./sample-scope";

export function LabStatusBadge({
  status,
}: {
  status: LabRequestView["status"];
}) {
  const { t } = useTranslation();
  return <Badge tone={labStatusTone(status)}>{t(labStatusKey(status))}</Badge>;
}

/**
 * Compact external-lab list for the designed visit section. Every row links to
 * the DOC-04 workspace; the row source is sample data (see `sample-scope`).
 */
export function LabRequestList({
  rows,
  empty,
}: {
  rows: readonly LabRequestView[];
  empty: string;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  if (rows.length === 0) return <p className="pc-note">{empty}</p>;
  return (
    <ul className="pc-lab-list">
      {rows.map((row) => (
        <li className="pc-listrow" key={row.id}>
          <div className="pc-listrow__grow">
            <strong>{row.test[locale]}</strong>
            <p className="pc-listrow__meta">
              {row.id} · {row.provider} ·{" "}
              {practitionerLabel(row.practitioner, locale)}
            </p>
          </div>
          <LabStatusBadge status={row.status} />
          <Link
            className="pc-button pc-button--secondary pc-button--sm"
            to={ROUTES.lab(row.id)}
          >
            {t("visit.openRequest")}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/**
 * DOC-02 external-lab block. Both designed entry points are present but
 * explicitly unavailable: the in-house lab model is unverified and new orders
 * have no contract, so neither opens a fabricated request.
 */
export function ExternalLabSection({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const unavailableId = useId();
  const rows = labRequestsForPatient(patientId);
  const native = nativeLabSample.patientId === patientId;
  return (
    <div className="pc-stack">
      <p className="pc-note">{t("visit.labPolicy")}</p>
      <div className="lab-toolbar">
        <button
          type="button"
          className="pc-button pc-button--secondary"
          disabled
          aria-describedby={unavailableId}
        >
          {t("visit.newLabRequest")}
        </button>
        <p className="pc-note" id={unavailableId}>
          {t("lab.newRequestUnavailable")}
        </p>
      </div>
      <LabRequestList rows={rows} empty={t("visit.noLabs")} />
      <Card title={t("visit.nativeLab")} hint={t("lab.modelUnverified")}>
        <p className="pc-note">
          {native
            ? nativeLabSample.note[locale]
            : t("visit.nativeLabUnavailable")}
        </p>
        <p className="pc-field__label">{t("lab.modelSupport")}</p>
        <Badge tone="warning">{t("lab.modelUnverified")}</Badge>
      </Card>
    </div>
  );
}
