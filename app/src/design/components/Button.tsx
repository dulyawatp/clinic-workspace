import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant =
  | "primary"
  | "cyan"
  | "secondary"
  | "ghost"
  | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: "md" | "sm";
  block?: boolean;
};

/**
 * Preclinic action button. One primary action per screen (DESIGN.md): the
 * `primary` variant is Royal indigo, `cyan` always carries clinical ink, and
 * every variant keeps the 44px target and a visible focus ring.
 */
export function Button({
  variant = "secondary",
  size = "md",
  block = false,
  className,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      type={type}
      className={[
        "pc-button",
        `pc-button--${variant}`,
        size === "sm" ? "pc-button--sm" : undefined,
        block ? "pc-button--block" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
