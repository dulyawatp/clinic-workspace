import type { Locale } from "./index";

const BANGKOK_DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Bangkok",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const MONEY = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function bangkokParts(iso: string): Record<string, string> {
  const parts: Record<string, string> = {};
  for (const part of BANGKOK_DATE_TIME.formatToParts(new Date(iso))) {
    parts[part.type] = part.value;
  }
  return parts;
}

export function formatDate(iso: string, _locale: Locale): string {
  const { day = "", month = "", year = "" } = bangkokParts(iso);
  return `${day}/${month}/${year}`;
}

export function formatDateTime(iso: string, _locale: Locale): string {
  const {
    day = "",
    month = "",
    year = "",
    hour = "",
    minute = "",
  } = bangkokParts(iso);
  return `${day}/${month}/${year} ${hour}:${minute}`;
}

const BANGKOK_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Bangkok",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Bangkok `HH:MM` for an instant, for schedule and day-list rows. */
export function formatTime(iso: string, _locale: Locale): string {
  return BANGKOK_TIME.format(new Date(iso));
}

export function formatMoney(amount: number, locale: Locale): string {
  const formatted = MONEY.format(amount);
  return locale === "th" ? `฿${formatted}` : `THB ${formatted}`;
}
