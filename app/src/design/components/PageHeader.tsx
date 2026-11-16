import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  /** Small uppercase context line above the title. */
  eyebrow?: string;
  /** One-line description under the title. */
  subtitle?: string;
  breadcrumb?: ReactNode;
  actions?: ReactNode;
};

/**
 * Overview header band: white surface, bottom hairline, page title and quick
 * actions. Separates context from the cards below, per the brand spec.
 */
export function PageHeader({
  title,
  eyebrow,
  subtitle,
  breadcrumb,
  actions,
}: PageHeaderProps) {
  return (
    <header className="pc-greeting">
      <div className="pc-greeting__inner">
        <div className="pc-greeting__content">
          {breadcrumb && <div className="pc-crumb">{breadcrumb}</div>}
          {eyebrow && <div className="pc-greeting__eyebrow">{eyebrow}</div>}
          <h1 className="pc-greeting__title">{title}</h1>
          {subtitle && <p className="pc-greeting__sub">{subtitle}</p>}
        </div>
        {actions && <div className="pc-greeting__actions">{actions}</div>}
      </div>
    </header>
  );
}
