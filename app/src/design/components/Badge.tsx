import type { ReactNode } from "react";

export type BadgeTone =
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "demo"
  | "neutral";

/**
 * Status pill. The visible label is the accessible name and is never optional:
 * state is always readable as text, colour only reinforces it.
 */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return <span className={`pc-badge pc-badge--${tone}`}>{children}</span>;
}
