import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  breadcrumb?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ title, breadcrumb, actions }: PageHeaderProps) {
  return (
    <header className="ui-page-header">
      <div className="ui-page-header-content">
        {breadcrumb && <div className="ui-page-breadcrumb">{breadcrumb}</div>}
        <h1>{title}</h1>
      </div>
      {actions && <div className="ui-page-actions">{actions}</div>}
    </header>
  );
}
