import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import en from "../../i18n/catalog/en.json";
import th from "../../i18n/catalog/th.json";

/**
 * Copy for the operations surfaces (pharmacy, cashier, management, admin).
 *
 * The prototype's operations layer registers 488 Thai/English phrase pairs as
 * content-addressed `ops.v3.*` catalogue keys; task 6 must use those staged
 * keys, not re-type the copy. The pair is the stable identity here: a caller
 * passes the prototype's exact Thai and English strings and receives the
 * catalogue key for that pair, so `t()` keeps locale switching reactive and
 * both catalogues stay the single source of text.
 *
 * `ops.*` pairs win over a same-text pair in another group, so an operations
 * string always resolves to the staged operations catalogue entry. Every
 * caller is covered by `ops-copy.test.ts`: a phrase that is not staged throws
 * here instead of silently rendering an English fallback in Thai.
 */

const SEPARATOR = "\u0000";

function buildIndex(): Map<string, string> {
  const byPair = new Map<string, string>();
  for (const key of Object.keys(th)) {
    const thaiText = (th as Record<string, string>)[key];
    const englishText = (en as Record<string, string>)[key];
    if (thaiText === undefined || englishText === undefined) continue;
    const pair = `${thaiText}${SEPARATOR}${englishText}`;
    const existing = byPair.get(pair);
    if (existing === undefined || (!existing.startsWith("ops.") && key.startsWith("ops."))) {
      byPair.set(pair, key);
    }
  }
  return byPair;
}

const KEYS = buildIndex();

/** Catalogue key for a staged Thai/English pair; throws when unstaged. */
export function opsPairKey(thaiText: string, englishText: string): string {
  const key = KEYS.get(`${thaiText}${SEPARATOR}${englishText}`);
  if (key === undefined) {
    throw new Error(
      `Unstaged operations copy: "${thaiText}" / "${englishText}"`,
    );
  }
  return key;
}

export type OpsCopy = (thaiText: string, englishText: string) => string;

/** `copy(th, en)` for a component: the staged catalogue key, localized. */
export function useOpsCopy(): OpsCopy {
  const { t } = useTranslation();
  return useCallback<OpsCopy>(
    (thaiText, englishText) => t(opsPairKey(thaiText, englishText)),
    [t],
  );
}
