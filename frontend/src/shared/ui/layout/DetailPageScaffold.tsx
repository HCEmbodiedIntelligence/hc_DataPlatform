import type { ReactNode } from 'react';
import { PageHeader, type PageHeaderProps } from './PageHeader';
import styles from './layout.module.css';

export interface DetailPageScaffoldProps {
  header: PageHeaderProps;
  resourceId: string;
  tabs?: ReactNode;
  children: ReactNode;
  inspector?: ReactNode;
  inspectorLabel?: string;
}

export function DetailPageScaffold({
  header,
  resourceId,
  tabs,
  children,
  inspector,
  inspectorLabel = '详情检查器',
}: Readonly<DetailPageScaffoldProps>) {
  return (
    <section className={styles.detailPage} data-layout="detail-page" aria-label={header.title}>
      <PageHeader
        {...header}
        metadata={
          <>
            {header.metadata}
            <span className={styles.resourceIdentity}>
              不可变资源 ID：<code>{resourceId}</code>
            </span>
          </>
        }
      />
      {tabs ? (
        <nav className={styles.tabsRegion} aria-label="详情分区">
          {tabs}
        </nav>
      ) : null}
      <div className={styles.detailGrid}>
        <section className={styles.detailContent} aria-label={`${header.title}详情`}>
          {children}
        </section>
        {inspector ? (
          <aside className={styles.inspectorRegion} aria-label={inspectorLabel}>
            {inspector}
          </aside>
        ) : null}
      </div>
    </section>
  );
}
