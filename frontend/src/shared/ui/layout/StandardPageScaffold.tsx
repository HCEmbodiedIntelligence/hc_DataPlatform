import type { ReactNode } from "react";
import { PageHeader, type PageHeaderProps } from "./PageHeader";
import styles from "./layout.module.css";

export interface StandardPageScaffoldProps {
  header: PageHeaderProps;
  summary?: ReactNode;
  filters?: ReactNode;
  state?: ReactNode;
  children?: ReactNode;
  pagination?: ReactNode;
}

export function StandardPageScaffold({
  header,
  summary,
  filters,
  state,
  children,
  pagination,
}: Readonly<StandardPageScaffoldProps>) {
  return (
    <div className={styles.standardPage} data-layout="standard-page">
      <PageHeader {...header} />
      {summary ? (
        <section className={styles.summaryRegion} aria-label="页面摘要">
          {summary}
        </section>
      ) : null}
      {filters ? <div className={styles.toolbarRegion}>{filters}</div> : null}
      <section
        className={styles.contentRegion}
        aria-label={`${header.title}主内容`}
      >
        {state ?? children}
      </section>
      {pagination ? (
        <nav className={styles.paginationRegion} aria-label="数据窗口分页">
          {pagination}
        </nav>
      ) : null}
    </div>
  );
}
