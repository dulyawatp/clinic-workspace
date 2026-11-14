import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { Button } from "../../ui/Button";
import { StatusChip } from "../../ui/StatusChip";
import { WriteGate } from "../../ui/WriteGate";

export interface CheckoutVisit {
  id: string;
  patientId: string;
  patientName: string;
  visitId: string;
  visitStatus: string;
  handoff: string;
  invoiceReference?: string;
  outstanding?: number;
  currency?: string;
}

const HANDOFFS = new Set([
  "completed",
  "billing_needed",
  "invoiced",
  "paid",
  "partial",
  "blocked",
]);

export function BillingHandoff({ visit }: { visit: CheckoutVisit }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { session } = useSession();
  const incomplete = visit.visitStatus === "incomplete";
  const blocked =
    visit.visitStatus !== "complete" ||
    visit.handoff === "blocked" ||
    !HANDOFFS.has(visit.handoff);
  const label = HANDOFFS.has(visit.handoff)
    ? t(`checkout.state.${visit.handoff}`)
    : `${visit.handoff} · ${t("status.unmapped")}`;
  const amount =
    typeof visit.outstanding === "number" &&
    Number.isFinite(visit.outstanding) &&
    visit.outstanding >= 0 &&
    visit.currency === "THB"
      ? formatMoney(visit.outstanding, locale)
      : t("checkout.unavailable");
  return (
    <tr>
      <td headers="checkout-patient" data-label={t("checkout.patientVisit")}>
        <div>
          {session?.user.roles.includes("CAS") ? (
            <Link to={ROUTES.patient(visit.patientId)}>
              {visit.patientName}
            </Link>
          ) : (
            <strong>{visit.patientName}</strong>
          )}
          <small>
            {t("appointment.patientId")}: {visit.patientId}
          </small>
          <small>
            {t("checkout.visit")}: {visit.visitId}
          </small>
        </div>
      </td>
      <td headers="checkout-handoff" data-label={t("checkout.handoff")}>
        <div>
          <StatusChip tone="neutral" label={label} />
          {blocked && (
            <small>
              {t(incomplete ? "checkout.incomplete" : "checkout.notReady")}
            </small>
          )}
        </div>
      </td>
      <td headers="checkout-invoice" data-label={t("checkout.invoice")}>
        {!blocked && visit.invoiceReference ? (
          <div>
            <span>{visit.invoiceReference}</span>
            <small>{t("checkout.invoiceUnavailable")}</small>
          </div>
        ) : (
          t("checkout.unavailable")
        )}
      </td>
      <td headers="checkout-outstanding" data-label={t("checkout.outstanding")}>
        {blocked ? t("checkout.unavailable") : amount}
      </td>
      <td headers="checkout-next" data-label={t("queue.nextStep")}>
        {blocked ? (
          t("checkout.actionBlocked")
        ) : (
          <div>
            <span>{t("checkout.cashierReference")}</span>
            <WriteGate
              capability="write.appointment.book"
              reason={t("checkout.writeUnavailable")}
            >
              <Button
                variant="secondary"
                disabled
                disabledReason={t("checkout.writeUnavailable")}
              >
                {t("checkout.followUp")}
              </Button>
            </WriteGate>
          </div>
        )}
      </td>
    </tr>
  );
}
