import type { AuditBootstrap } from '../../../features/audit/types';
import { UiMetricCard, type MetricState } from '../../../shared/ui';
import styles from '../styles.module.css';

export function AuditSummaryStrip({
  bootstrap,
  state,
}: Readonly<{
  bootstrap?: AuditBootstrap;
  state: MetricState;
}>) {
  const cards = bootstrap
    ? [
        ['今日事件', bootstrap.metrics.today],
        ['高风险', bootstrap.metrics.highRisk],
        ['失败', bootstrap.metrics.failed],
        ['活跃 Actor', bootstrap.metrics.activeActors],
      ] as const
    : [
        ['今日事件', undefined],
        ['高风险', undefined],
        ['失败', undefined],
        ['活跃 Actor', undefined],
      ] as const;

  return (
    <section className={styles.summaryGrid} aria-label="审计指标">
      {cards.map(([label, value]) => (
        <UiMetricCard
          key={label}
          label={label}
          value={value?.toLocaleString('zh-CN')}
          state={state}
          asOf={bootstrap ? <time dateTime={bootstrap.asOf}>{bootstrap.asOf}</time> : undefined}
          basis={bootstrap?.catalogVersion}
        />
      ))}
    </section>
  );
}
