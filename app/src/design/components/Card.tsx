import { type ReactNode, useId } from "react";

type CardProps = {
  title?: string;
  hint?: string;
  actions?: ReactNode;
  footer?: ReactNode;
  /** Removes body padding for flush content such as tables. */
  flush?: boolean;
  className?: string;
  children: ReactNode;
};

/** Preclinic 10px card: hairline border, navy shadow, optional head and foot. */
export function Card({
  title,
  hint,
  actions,
  footer,
  flush = false,
  className,
  children,
}: CardProps) {
  const titleId = useId();
  return (
    <section
      className={["pc-card", className].filter(Boolean).join(" ")}
      aria-labelledby={title ? titleId : undefined}
    >
      {(title || actions) && (
        <header className="pc-card__head">
          <div className="pc-card__heading">
            {title && (
              <h2 className="pc-card__title" id={titleId}>
                {title}
              </h2>
            )}
            {hint && <p className="pc-card__hint">{hint}</p>}
          </div>
          {actions && <div className="pc-card__actions">{actions}</div>}
        </header>
      )}
      <div
        className={["pc-card__body", flush ? "pc-card__body--flush" : undefined]
          .filter(Boolean)
          .join(" ")}
      >
        {children}
      </div>
      {footer && <footer className="pc-card__foot">{footer}</footer>}
    </section>
  );
}
