import { createInstance, type i18n as I18nInstance } from "i18next";
import en from "./catalog/en.json";
import th from "./catalog/th.json";

export { translateClinicalText } from "./clinical";

export type Locale = "th" | "en";

export type Resources = {
  th: Record<string, string>;
  en: Record<string, string>;
};

/**
 * Creates an isolated i18next instance for the requested locale.
 *
 * Catalogue keys are flat, dot-containing identifiers from the prototype
 * runtime snapshot, so `keySeparator` is disabled to address them verbatim.
 * When `resources` is supplied (tests), it replaces the bundled catalogues
 * per locale; English remains the fallback language in every case.
 */
export function initI18n(locale: Locale, resources?: Resources): I18nInstance {
  const instance = createInstance();
  instance.init({
    lng: locale,
    fallbackLng: "en",
    initAsync: false,
    keySeparator: false,
    interpolation: { escapeValue: false },
    resources: {
      th: { translation: resources ? resources.th : th },
      en: { translation: resources ? resources.en : en },
    },
  });
  return instance;
}
