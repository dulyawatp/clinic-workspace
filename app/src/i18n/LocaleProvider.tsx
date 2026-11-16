import type { i18n as I18nInstance } from "i18next";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { I18nextProvider } from "react-i18next";
import { initI18n, type Locale } from "./index";
import { storeLocale } from "./locale-storage";

export type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({
  initial,
  children,
}: {
  initial: Locale;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initial);
  const [i18n] = useState<I18nInstance>(() => initI18n(initial));

  const setLocale = useCallback(
    (next: Locale) => {
      setLocaleState(next);
      // Only an explicit choice is persisted: rendering in a language is never
      // treated as a preference, so nothing is written for a first-time visitor.
      storeLocale(next);
      void i18n.changeLanguage(next);
    },
    [i18n],
  );

  // `<html lang>` follows the rendered language for screen readers, search and
  // correct Thai/English font shaping.
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);

  return (
    <LocaleContext.Provider value={value}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) {
    throw new Error("useLocale must be used within a LocaleProvider");
  }
  return value;
}
