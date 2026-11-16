import type { ReactNode } from "react";
import { formatDate, formatDateTime, formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { useOpsCopy } from "./ops-copy";
import "./ops.css";

/**
 * Shared operations-surface primitives. They mirror the prototype's
 * `operations-v3` helpers (`sourceNote`, filter rows, exclusive bucket strip,
 * tab strips, record tables) on top of the Preclinic components from task 1.
 * Presentation only: no read, no write, no transport.
 */

/** The prototype's "browser-local sample records" line above every ops page. */
export function SourceNote() {
  const copy = useOpsCopy();
  return (
    <p className="ops-source">
      {copy(
        "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
        "Browser-local sample records · server contracts unverified",
      )}
    </p>
  );
}

/**
 * Plain explanatory panel. Deliberately not `role="alert"`: these notes are
 * standing disclosures, not a response to a failed action.
 */
export function OpsNote({ children }: { children: ReactNode }) {
  return <div className="ops-note">{children}</div>;
}

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="ops-filters">{children}</div>;
}

/** Label + control pair; the control keeps the 44px target and focus ring. */
export function FilterField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="ops-control">
      <span className="ops-control__label">{label}</span>
      {children}
    </label>
  );
}

/** Exclusive bucket strip (expiry windows, funnel stages). */
export function Buckets({
  entries,
  label,
}: {
  entries: ReadonlyArray<{ key: string; label: string; value: number }>;
  label: string;
}) {
  return (
    <div className="ops-buckets" role="group" aria-label={label}>
      {entries.map((entry) => (
        <span key={entry.key}>
          {entry.label}
          <strong>{entry.value}</strong>
        </span>
      ))}
    </div>
  );
}

export interface OpsTab {
  readonly id: string;
  readonly label: string;
}

/**
 * Prototype tab strip as toggle buttons: `aria-pressed` carries the state, so
 * the labels stay queryable as buttons (and colour is never the only cue).
 */
export function OpsTabs({
  tabs,
  active,
  label,
  onChange,
}: {
  tabs: readonly OpsTab[];
  active: string;
  label: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="ops-tabs" role="group" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className="pc-button pc-button--ghost pc-button--sm ops-tab"
          aria-pressed={tab.id === active}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export interface ChartSeries {
  readonly label: string;
  readonly tone: "ink" | "cyan" | "muted";
  readonly values: readonly number[];
}

/**
 * Minimal inline SVG line chart (no chart library). Each series is also
 * printed in the legend and, on the management surfaces, in the table beside
 * the chart, so the shape is never the only way to read the numbers.
 */
export function MiniChart({
  title,
  labels,
  series,
}: {
  title: string;
  labels: readonly string[];
  series: readonly ChartSeries[];
}) {
  const width = 640;
  const height = 180;
  const padX = 12;
  const padY = 14;
  const max = Math.max(
    1,
    ...series.flatMap((row) => row.values.map((value) => value)),
  );
  const stepX =
    labels.length > 1 ? (width - padX * 2) / (labels.length - 1) : 0;
  const y = (value: number) => height - padY - (value / max) * (height - padY * 2);

  return (
    <figure className="ops-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        preserveAspectRatio="none"
      >
        {[0, 0.5, 1].map((ratio) => (
          <line
            key={ratio}
            className="ops-chart__grid"
            x1={padX}
            x2={width - padX}
            y1={padY + ratio * (height - padY * 2)}
            y2={padY + ratio * (height - padY * 2)}
          />
        ))}
        {series.map((row) => (
          <g key={row.label} className={`ops-chart__series ops-chart__series--${row.tone}`}>
            <polyline
              points={row.values
                .map((value, index) => `${padX + index * stepX},${y(value)}`)
                .join(" ")}
            />
            {row.values.map((value, index) => (
              <circle
                key={`${row.label}-${labels[index] ?? index}`}
                cx={padX + index * stepX}
                cy={y(value)}
                r={2.5}
              />
            ))}
          </g>
        ))}
      </svg>
      <figcaption className="ops-chart__legend">
        {series.map((row) => (
          <span key={row.label}>
            <span
              className={`ops-chart__swatch ops-chart__swatch--${row.tone}`}
              aria-hidden="true"
            />
            {row.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** RFC-4180-ish CSV: quote any cell containing a separator or a newline. */
export function toCsv(rows: ReadonlyArray<ReadonlyArray<string | number>>): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const text = String(cell);
          return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
        })
        .join(","),
    )
    .join("\n");
}

/**
 * Client-side CSV download. This is a local blob of the rows already on screen
 * (allowed for sample exports); it never calls a server.
 */
export function downloadCsv(
  filename: string,
  rows: ReadonlyArray<ReadonlyArray<string | number>>,
): void {
  if (typeof URL.createObjectURL !== "function") return;
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function CsvButton({
  filename,
  rows,
  label,
}: {
  filename: string;
  rows: ReadonlyArray<ReadonlyArray<string | number>>;
  label: string;
}) {
  return (
    <button
      type="button"
      className="pc-button pc-button--secondary pc-button--sm"
      onClick={() => downloadCsv(filename, rows)}
    >
      {label}
    </button>
  );
}

/** Locale-aware date/money helpers matching the prototype's `D.fmt`. */
export function useOpsFormat() {
  const { locale } = useLocale();
  return {
    locale,
    money: (value: number) => formatMoney(value, locale),
    date: (iso: string) => formatDate(iso, locale),
    dateTime: (iso: string) => formatDateTime(iso, locale),
  };
}
