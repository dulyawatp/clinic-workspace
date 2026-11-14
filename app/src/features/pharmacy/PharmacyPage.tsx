import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useSession } from "../../app/SessionProvider";
import { type AppError, toI18nKey } from "../../domain/errors";
import { mapHttpError } from "../../transport/error-map";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import "./pharmacy.css";

const text = z
  .string()
  .min(1)
  .max(256)
  .refine(
    (value) =>
      value.trim().length > 0 &&
      ![...value].some((char) => {
        const code = char.charCodeAt(0);
        return code < 32 || code === 127;
      }),
  );
const stockResponse = z
  .strictObject({
    items: z
      .array(
        z.strictObject({
          item_code: text,
          item_name: text,
          stock_uom: text,
          is_stock_item: z.boolean(),
        }),
      )
      .max(1000),
  })
  .refine(
    ({ items }) =>
      new Set(items.map((item) => item.item_code)).size === items.length,
  );

const unavailable = (): AppError => ({
  code: "UPSTREAM_UNAVAILABLE",
  retryable: true,
});

async function fetchStock(signal: AbortSignal) {
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch("/api/live/synthetic-stock", {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch {
    throw unavailable();
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401)
    throw { code: "AUTH_EXPIRED", retryable: false } satisfies AppError;
  if (response.status === 403)
    throw { code: "FORBIDDEN", retryable: false } satisfies AppError;
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  const parsed = stockResponse.safeParse(body);
  if (!parsed.success) throw unavailable();
  return parsed.data.items;
}

export function PharmacyPage() {
  const { t } = useTranslation();
  const { status, session, authGeneration, refresh } = useSession();
  const allowed =
    status === "authenticated" &&
    session?.mode === "live" &&
    session.user.roles.includes("PHA");
  const query = useQuery<z.infer<typeof stockResponse>["items"], AppError>({
    queryKey: ["live-stock", session?.mode, authGeneration],
    enabled: allowed,
    queryFn: ({ signal }) => fetchStock(signal),
    retry: false,
  });

  return (
    <>
      <PageHeader title={t("pharmacy.title")} />
      <div className="shell-page-body">
        {!allowed ? (
          <StatePanel
            kind="permission-denied"
            title={t("state.denied.title")}
          />
        ) : (
          <section className="pharmacy-card" aria-labelledby="pharmacy-heading">
            <div className="pharmacy-intro">
              <span className="pharmacy-eyebrow">{t("pharmacy.eyebrow")}</span>
              <h2 id="pharmacy-heading">{t("pharmacy.heading")}</h2>
              <p>{t("pharmacy.description")}</p>
            </div>
            {query.isError ? (
              query.error.code === "AUTH_EXPIRED" || query.error.retryable ? (
                <StatePanel
                  kind="error"
                  title={t(toI18nKey(query.error))}
                  onRetry={() => {
                    if (query.error.code === "AUTH_EXPIRED") void refresh();
                    else void query.refetch();
                  }}
                  retryLabel={t("action.retry")}
                />
              ) : (
                <StatePanel kind="error" title={t(toI18nKey(query.error))} />
              )
            ) : query.isPending ||
              query.isFetching ||
              !query.isFetchedAfterMount ? (
              <StatePanel kind="loading" title={t("common.loading")} />
            ) : query.data.length === 0 ? (
              <StatePanel kind="empty" title={t("pharmacy.empty")} />
            ) : (
              <div className="pharmacy-table-wrap">
                <table className="pharmacy-table">
                  <caption className="shell-sr-only">
                    {t("pharmacy.heading")}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("pharmacy.name")}</th>
                      <th scope="col">{t("pharmacy.code")}</th>
                      <th scope="col">{t("pharmacy.unit")}</th>
                      <th scope="col">{t("pharmacy.type")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.map((item) => (
                      <tr key={item.item_code}>
                        <td data-label={t("pharmacy.name")}>
                          <strong>{item.item_name}</strong>
                        </td>
                        <td data-label={t("pharmacy.code")}>
                          {item.item_code}
                        </td>
                        <td data-label={t("pharmacy.unit")}>
                          {item.stock_uom}
                        </td>
                        <td data-label={t("pharmacy.type")}>
                          <span className="pharmacy-tag">
                            {t(
                              item.is_stock_item
                                ? "pharmacy.stockItem"
                                : "pharmacy.nonStockItem",
                            )}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
