import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import type { VisitContext } from "../../domain/clinical";
import { useCapabilities } from "../shell/CapabilityProvider";

export function VisitHeader({
  context,
  sharedDevice = false,
}: {
  context: VisitContext;
  sharedDevice?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const capability = useCapabilities().get("read.photo");
  const date = new Date(context.startedAt);
  const started = Number.isNaN(date.getTime())
    ? context.startedAt
    : new Intl.DateTimeFormat(i18n.language === "th" ? "th-TH" : "en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
  const fields = [
    [t("clinical.patientId"), context.patientId],
    [
      t("clinical.encounterId"),
      <span key="encounter" data-testid="encounter-id">
        {context.encounterId}
      </span>,
    ],
    [t("clinical.started"), started],
    [t("clinical.appointment"), context.appointmentId || "—"],
    [t("clinical.practitioner"), context.practitionerName],
    [
      t("clinical.state"),
      context.state === "open" ? t("clinical.open") : context.state,
    ],
  ];
  return (
    <header className="visit-identity">
      <div className="visit-identity-heading">
        <span className="visit-avatar" aria-hidden="true">
          ✚
        </span>
        <div>
          <p>{t("clinical.patient")}</p>
          <h2>{context.patientName}</h2>
        </div>
        <span className="visit-readonly">{t("clinical.readOnly")}</span>
      </div>
      <dl>
        {fields.map(([label, value]) => (
          <div key={label as string}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {(capability === "enabled" || capability === "read_only") && (
        <Link
          className="ui-button ui-button--secondary"
          to={ROUTES.photos(context.patientId)}
        >
          {t("photos.gallery")}
        </Link>
      )}
      {sharedDevice && <p className="visit-privacy">{t("clinical.privacy")}</p>}
    </header>
  );
}
