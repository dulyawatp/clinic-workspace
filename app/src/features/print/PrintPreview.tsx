import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { Locale } from "../../i18n";

export const PRINT_TEMPLATES = [
  "receipt",
  "invoice",
  "lab",
  "statement",
  "summary",
] as const;
export type PrintTemplate = (typeof PRINT_TEMPLATES)[number];

/** Static layout sample only: no record reads, statutory forms or print side effects. */
export function PrintPreview({
  template,
  locale,
}: {
  template: PrintTemplate;
  locale: Locale;
}) {
  const { t, i18n } = useTranslation();
  const reasonId = useId();
  // Missing/invalid runtime props must not cause i18next to inherit UI language.
  if (locale !== "th" && locale !== "en")
    throw new Error("PrintPreview requires an explicit th/en output locale");
  const documentT = i18n.getFixedT(locale);

  return (
    <div className="print-preview">
      <article
        className="print-sheet"
        lang={locale}
        aria-label={documentT("system.previewTitle")}
      >
        <p className="print-sheet-brand">DEMO CLINIC · SAMPLE</p>
        <h3>{documentT(`print.doc.${template}`)}</h3>
        <p className="print-watermark">{documentT("print.sampleWatermark")}</p>
        <hr />
        <p>{documentT("system.documentSample")}</p>
        <div className="print-sheet-placeholder" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <hr />
        <p className="print-sheet-note">{documentT("system.documentNote")}</p>
      </article>
      <div className="print-unavailable">
        <button
          type="button"
          className="ui-button ui-button--primary"
          disabled
          aria-describedby={reasonId}
        >
          {t("action.printExport")}
        </button>
        <div id={reasonId}>
          <h3>{t("print.unverifiedTitle")}</h3>
          <p>{t("print.unverifiedBody")}</p>
        </div>
      </div>
    </div>
  );
}
