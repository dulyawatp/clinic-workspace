import { useTranslation } from "react-i18next";
import { useLocale } from "../../i18n/LocaleProvider";

export function LanguageToggle() {
  const { locale, setLocale } = useLocale();
  const { t } = useTranslation();
  return (
    <fieldset className="shell-language">
      <legend className="shell-sr-only">{t("lang.label")}</legend>
      {(["th", "en"] as const).map((language) => (
        <button
          key={language}
          type="button"
          lang={language}
          aria-pressed={locale === language}
          onClick={() => setLocale(language)}
        >
          {language.toUpperCase()}
        </button>
      ))}
    </fieldset>
  );
}
