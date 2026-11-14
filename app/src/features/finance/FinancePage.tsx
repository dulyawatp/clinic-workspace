import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useSession } from "../../app/SessionProvider";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import "./finance.css";

const financeSchema = z
  .object({
    currency: z.string().regex(/^[A-Z]{3}$/),
    invoice_count: z.number().int().nonnegative().safe(),
    invoiced: z.number().finite().nonnegative(),
    outstanding: z.number().finite().nonnegative(),
  })
  .strict();

async function readFinance(signal: AbortSignal) {
  assertLegacyRoute();
  const response = await fetch("/api/live/synthetic-finance", {
    credentials: "same-origin",
    cache: "no-store",
    headers: { accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("Finance unavailable");
  return financeSchema.parse(await response.json());
}

export function FinancePage() {
  const { t, i18n } = useTranslation();
  const { status, session, authGeneration } = useSession();
  const allowed =
    status === "authenticated" &&
    session?.mode === "live" &&
    session.user.roles.some((role) => role === "MGR" || role === "CAS");
  const finance = useQuery({
    queryKey: ["live-synthetic-finance", authGeneration],
    queryFn: ({ signal }) => readFinance(signal),
    enabled: allowed,
    retry: false,
  });
  const data = finance.data;
  const number = new Intl.NumberFormat(i18n.language === "en" ? "en" : "th", {
    maximumFractionDigits: 2,
  });

  return (
    <>
      <PageHeader title={t("finance.title")} />
      <div className="shell-page-body finance-page">
        <section
          className="finance-scope"
          aria-labelledby="finance-scope-title"
        >
          <p className="finance-kicker">{t("finance.kicker")}</p>
          <h2 id="finance-scope-title">{t("finance.scopeTitle")}</h2>
          <p>{t("finance.scope")}</p>
        </section>
        {status === "loading" ? (
          <StatePanel kind="loading" title={t("common.loading")} />
        ) : !allowed ? (
          <StatePanel kind="error" title={t("finance.unavailable")} />
        ) : finance.isPending ? (
          <StatePanel kind="loading" title={t("common.loading")} />
        ) : finance.isError || data === undefined ? (
          <StatePanel kind="error" title={t("finance.unavailable")} />
        ) : data.invoice_count === 0 ? (
          <StatePanel kind="empty" title={t("finance.empty")} />
        ) : (
          <dl className="finance-metrics">
            <div>
              <dt>{t("finance.invoices")}</dt>
              <dd>{number.format(data.invoice_count)}</dd>
            </div>
            <div>
              <dt>{t("finance.invoiced")}</dt>
              <dd>
                {number.format(data.invoiced)} <span>{data.currency}</span>
              </dd>
            </div>
            <div>
              <dt>{t("finance.outstanding")}</dt>
              <dd>
                {number.format(data.outstanding)} <span>{data.currency}</span>
              </dd>
            </div>
          </dl>
        )}
      </div>
    </>
  );
}
