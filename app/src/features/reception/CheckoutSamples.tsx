import { useTranslation } from "react-i18next";
import { Badge, Card, SampleBanner } from "../../design/components";
import { checkoutSamples } from "../../fixtures/data-authored";
import { useLocale } from "../../i18n/LocaleProvider";

const GAP_KEYS: Readonly<Record<string, string>> = {
  "note.assessment": "checkout.gap.note.assessment",
  "note.plan": "checkout.gap.note.plan",
  procedures: "checkout.gap.procedures",
};

/**
 * REC-05 authored states. The live handoff table stays untouched; these two
 * Demo/Sample cards make the missing-invoice and incomplete-visit designs
 * inspectable without inventing a billing or visit write contract.
 */
export function CheckoutSamples() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { missingInvoice, incompleteVisit } = checkoutSamples;

  return (
    <section className="checkout-samples" aria-label={t("checkout.samplesTitle")}>
      <SampleBanner label={t("app.demoTag")} title={t("checkout.samplesTitle")}>
        <p>{t("checkout.samplesBody")}</p>
      </SampleBanner>
      <div className="checkout-samples__grid">
        <Card title={t("checkout.missingInvoiceTitle")}>
          <p>
            <Badge tone="warning">{t("checkout.state.billing_needed")}</Badge>
          </p>
          <p>{missingInvoice.note[locale]}</p>
          <dl className="checkout-samples__facts">
            <div>
              <dt>{t("checkout.samplePatient")}</dt>
              <dd>{missingInvoice.visit.patientId}</dd>
            </div>
            <div>
              <dt>{t("checkout.sampleVisit")}</dt>
              <dd>{missingInvoice.visit.id}</dd>
            </div>
          </dl>
        </Card>

        <Card title={t("checkout.incompleteVisitTitle")}>
          <p>
            <Badge tone="danger">{t("checkout.incomplete")}</Badge>
          </p>
          <p>{incompleteVisit.note[locale]}</p>
          <dl className="checkout-samples__facts">
            <div>
              <dt>{t("checkout.samplePatient")}</dt>
              <dd>{incompleteVisit.visit.patientId}</dd>
            </div>
            <div>
              <dt>{t("checkout.sampleVisit")}</dt>
              <dd>{incompleteVisit.visit.id}</dd>
            </div>
          </dl>
          <div>
            <p>{t("checkout.sampleGaps")}</p>
            <ul className="checkout-samples__gaps">
              {incompleteVisit.gaps.map((gap) => (
                <li key={gap}>{t(GAP_KEYS[gap] ?? "checkout.notReady")}</li>
              ))}
            </ul>
          </div>
        </Card>
      </div>
    </section>
  );
}
