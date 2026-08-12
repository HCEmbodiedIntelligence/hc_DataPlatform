import { Flex, Typography } from 'antd';
import { useId, type ReactNode } from 'react';
import styles from './layout.module.css';

export interface PageBreadcrumbItem {
  key: string;
  label: ReactNode;
}

export interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  breadcrumbs?: readonly PageBreadcrumbItem[];
  metadata?: ReactNode;
  actions?: ReactNode;
  headingId?: string;
}

export function PageHeader({
  title,
  description,
  breadcrumbs = [],
  metadata,
  actions,
  headingId,
}: Readonly<PageHeaderProps>) {
  const generatedId = useId();
  const titleId = headingId ?? generatedId;

  return (
    <header className={styles.pageHeader} aria-labelledby={titleId}>
      <div className={styles.pageHeaderCopy}>
        {breadcrumbs.length > 0 ? (
          <nav aria-label="面包屑">
            <ol className={styles.breadcrumbs}>
              {breadcrumbs.map((item, index) => (
                <li
                  key={item.key}
                  aria-current={index === breadcrumbs.length - 1 ? 'page' : undefined}
                >
                  {item.label}
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
        <Typography.Title id={titleId} level={1} className={styles.pageTitle}>
          {title}
        </Typography.Title>
        {description ? <div className={styles.pageDescription}>{description}</div> : null}
        {metadata ? <div className={styles.pageMetadata}>{metadata}</div> : null}
      </div>
      {actions ? (
        <Flex
          className={styles.pageActions}
          gap="small"
          wrap="wrap"
          align="center"
          aria-label="页面操作"
        >
          {actions}
        </Flex>
      ) : null}
    </header>
  );
}
