import type { ReactNode } from "react";
import { Badge } from "./Badge";

/**
 * Demo/Sample label for fixture-backed content. It is rendered above sample
 * data, never instead of it, so nobody mistakes a fixture for a real record.
 */
export function SampleBanner({
  label,
  title,
  children,
  tone = "demo",
}: {
  /** Short pill label, e.g. "Demo data". */
  label: string;
  /** Optional heading inside the banner. */
  title?: string;
  children?: ReactNode;
  tone?: "demo" | "warn" | "danger";
}) {
  const badgeTone =
    tone === "demo" ? "demo" : tone === "warn" ? "warning" : "danger";
  const className =
    tone === "demo" ? "pc-banner pc-banner--demo" : `pc-banner pc-banner--${tone}`;
  return (
    <div className={className} data-testid="sample-banner">
      <div className="pc-banner__body">
        <div className="pc-banner__title">
          <Badge tone={badgeTone}>{label}</Badge>
          {title && <span>{title}</span>}
        </div>
        {children}
      </div>
    </div>
  );
}
