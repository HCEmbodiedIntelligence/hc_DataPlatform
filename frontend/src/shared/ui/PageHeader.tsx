import type { ReactNode } from 'react';

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export interface PageHeaderProps {
  title: string;
  description?: string;
  breadcrumbs?: readonly BreadcrumbItem[];
  actions?: ReactNode;
}

export function PageHeader({ title, description, breadcrumbs = [], actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        {breadcrumbs.length > 0 ? (
          <nav aria-label="面包屑">
            <ol className="breadcrumbs">
              {breadcrumbs.map((item, index) => (
                <li key={`${item.label}-${index}`}>
                  {item.href ? <a href={item.href}>{item.label}</a> : <span aria-current="page">{item.label}</span>}
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-header__actions" aria-label="页面操作">{actions}</div> : null}
    </header>
  );
}
