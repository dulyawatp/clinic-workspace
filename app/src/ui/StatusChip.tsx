import type { ReactNode } from "react";

type StatusChipProps = {
  tone: "success" | "warning" | "danger" | "info" | "neutral";
  label: string;
  icon?: ReactNode;
};

export function StatusChip({ tone, label, icon }: StatusChipProps) {
  return (
    <span className={`ui-chip ui-chip--${tone}`}>
      {icon && (
        <span className="ui-chip-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      {label}
    </span>
  );
}
