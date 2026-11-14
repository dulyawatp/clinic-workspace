import { type ButtonHTMLAttributes, useId } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
  disabledReason?: string;
};

export function Button({
  variant = "primary",
  disabledReason,
  disabled,
  className,
  type = "button",
  "aria-describedby": describedBy,
  ...props
}: ButtonProps) {
  const reasonId = useId();
  const showReason = disabled && Boolean(disabledReason);

  return (
    <>
      <button
        {...props}
        type={type}
        disabled={disabled}
        className={["ui-button", `ui-button--${variant}`, className]
          .filter(Boolean)
          .join(" ")}
        aria-describedby={
          [describedBy, showReason ? reasonId : undefined]
            .filter(Boolean)
            .join(" ") || undefined
        }
      />
      {showReason && (
        <span className="ui-button-reason" id={reasonId}>
          {disabledReason}
        </span>
      )}
    </>
  );
}
