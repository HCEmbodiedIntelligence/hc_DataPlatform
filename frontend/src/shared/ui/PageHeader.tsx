import type { ReactNode } from 'react';
import { Link, type To } from 'react-router-dom';

export interface BreadcrumbItem {
  label: string;
  href?: To;
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
              {breadcrumbs.map((item, index) => {
                const current = index === breadcrumbs.length - 1;
                return (
                  <li
                    key={`${item.label}-${index}`}
                    aria-current={current ? 'page' : undefined}
                  >
                    {!current && item.href ? <Link to={item.href}>{item.label}</Link> : item.label}
                  </li>
                );
              })}
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
