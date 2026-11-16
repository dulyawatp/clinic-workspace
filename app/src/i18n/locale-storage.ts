import type { Locale } from "./index";

/**
 * Device-level language preference. It is a presentation preference only: the
 * server never reads it, and it is written only when the user picks a language,
 * so a fresh browser stores nothing.
 */
export const LOCALE_KEY = "clinic.locale";

export function isLocale(value: unknown): value is Locale {
  return value === "th" || value === "en";
}

export function readStoredLocale(): Locale | null {
  try {
    const raw = localStorage.getItem(LOCALE_KEY);
    return isLocale(raw) ? raw : null;
  } catch {
    // Storage can be blocked by browser privacy settings; the default locale
    // still applies and nothing is written.
    return null;
  }
}

export function storeLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_KEY, locale);
  } catch {
    // Ignored on purpose: a failed preference write must never block the UI.
  }
}
