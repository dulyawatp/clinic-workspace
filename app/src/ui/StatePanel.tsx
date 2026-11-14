import { useId } from "react";
import { Button } from "./Button";

type StatePanelProps = {
  kind: "empty" | "loading" | "error" | "permission-denied";
  title: string;
  detail?: string;
} & (
  | { onRetry: () => void; retryLabel: string }
  | { onRetry?: never; retryLabel?: never }
);

export function StatePanel({
  kind,
  title,
  detail,
  onRetry,
  retryLabel,
}: StatePanelProps) {
  const titleId = useId();

  return (
    <section
      className={`ui-state ui-state--${kind}`}
      role={
        kind === "loading" ? "status" : kind === "error" ? "alert" : undefined
      }
      aria-labelledby={titleId}
    >
      <h2 id={titleId}>{title}</h2>
      {detail && <p>{detail}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          {retryLabel}
        </Button>
      )}
    </section>
  );
}
