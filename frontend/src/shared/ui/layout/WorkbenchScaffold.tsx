import type { ReactNode } from 'react';
import { PageHeader, type PageHeaderProps } from './PageHeader';
import styles from './layout.module.css';

export interface WorkbenchScaffoldProps {
  header: PageHeaderProps;
  navigation?: ReactNode;
  toolbar?: ReactNode;
  media: ReactNode;
  editor: ReactNode;
  inspector?: ReactNode;
  timeline?: ReactNode;
}

export function WorkbenchScaffold({
  header,
  navigation,
  toolbar,
  media,
  editor,
  inspector,
  timeline,
}: Readonly<WorkbenchScaffoldProps>) {
  return (
    <section className={styles.workbench} data-layout="workbench" aria-label={header.title}>
      <PageHeader {...header} />
      {toolbar ? (
        <section className={styles.workbenchToolbar} aria-label="工作台工具栏">
          {toolbar}
        </section>
      ) : null}
      <div className={styles.workbenchGrid}>
        {navigation ? (
          <aside className={styles.workbenchNavigation} aria-label="工作台导航">
            {navigation}
          </aside>
        ) : null}
        <section className={styles.workbenchMedia} aria-label="媒体工作区">
          {media}
        </section>
        <section className={styles.workbenchEditor} aria-label="编辑工作区">
          {editor}
        </section>
        {inspector ? (
          <aside className={styles.workbenchInspector} aria-label="工作台检查器">
            {inspector}
          </aside>
        ) : null}
        {timeline ? (
          <section className={styles.workbenchTimeline} aria-label="时间轴">
            {timeline}
          </section>
        ) : null}
      </div>
    </section>
  );
}
