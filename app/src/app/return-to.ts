import { ROUTES } from "./routes";

export const RETURN_TO_KEY = "clinic.returnTo";

// Explicit static pages only. Never derive this list from all future ROUTES:
// patient/visit/photo/record destinations can contain identifying segments.
const SAFE_PATHS: ReadonlySet<string> = new Set([
  ROUTES.home,
  ROUTES.search,
  ROUTES.profile,
  ROUTES.system,
  ROUTES.reception,
  ROUTES.queue,
  ROUTES.checkout,
]);
const SAFE_VALUES: Readonly<Record<string, ReadonlySet<string>>> = {
  status: new Set([
    "all",
    "open",
    "waiting",
    "scheduled",
    "arrived",
    "called",
    "in_consultation",
    "no_show",
    "completed",
    "cancelled",
  ]),
  view: new Set(["day", "week", "month", "list", "grid"]),
  section: new Set(["main", "account", "language", "preferences"]),
  range: new Set(["day", "week", "month"]),
  scope: new Set(["all", "checkedIn", "waiting", "upcoming"]),
};

/** Static clinic page and bounded view metadata, never record paths/free text. */
export function sanitizeReturnTo(raw: string | null): string | null {
  if (
    !raw?.startsWith("/clinic") ||
    raw.trim() !== raw ||
    /%(?:2f|5c|25)/i.test(raw)
  )
    return null;
  try {
    if (/[\p{Cc}\\]/u.test(decodeURIComponent(raw))) return null;
    const url = new URL(raw, "https://clinic.invalid");
    if (
      url.origin !== "https://clinic.invalid" ||
      !SAFE_PATHS.has(url.pathname) ||
      url.pathname.includes("//")
    )
      return null;
    const query = new URLSearchParams();
    for (const [key, value] of url.searchParams) {
      // A key allowlist alone permits names/phone/IDs disguised as view values.
      const safe =
        key === "page"
          ? /^(?:[1-9]\d?|100)$/.test(value)
          : Object.hasOwn(SAFE_VALUES, key) && SAFE_VALUES[key]?.has(value);
      if (safe && !query.has(key)) query.set(key, value);
    }
    const search = query.toString();
    return url.pathname + (search ? `?${search}` : "");
  } catch {
    return null;
  }
}

export function rememberReturnTo(raw: string): void {
  const target = sanitizeReturnTo(raw) ?? ROUTES.home;
  try {
    sessionStorage.setItem(RETURN_TO_KEY, target);
  } catch {
    // Storage can be disabled by browser privacy policy; re-auth still works.
  }
}

/** Consume once after successful sign-in; revalidate against storage tampering. */
export function consumeReturnTo(): string | null {
  try {
    const raw = sessionStorage.getItem(RETURN_TO_KEY);
    sessionStorage.removeItem(RETURN_TO_KEY);
    return sanitizeReturnTo(raw);
  } catch {
    return null;
  }
}
