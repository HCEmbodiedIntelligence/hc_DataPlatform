import { Card, Skeleton } from 'antd';
import type { ReactNode } from 'react';
import type { MetricState } from './contracts';
import styles from './state.module.css';

export interface MetricCardProps {
  label: string;
  value?: ReactNode;
  unit?: ReactNode;
  basis?: ReactNode;
  asOf?: ReactNode;
  state?: MetricState;
  description?: ReactNode;
}

const stateValue: Readonly<Record<Exclude<MetricState, 'ready' | 'loading'>, string>> = {
  unknown: '未知',
  forbidden: '无权查看',
  error: '暂不可用',
};

export function MetricCard({
  label,
  value,
  unit,
  basis,
  asOf,
  state = value === undefined || value === null ? 'unknown' : 'ready',
  description,
}: Readonly<MetricCardProps>) {
  const displayValue = state === 'ready' ? value : state === 'loading' ? null : stateValue[state];

  return (
    <section className={styles.metricCard} aria-label={label} data-metric-state={state}>
      <Card size="small">
        <h2 className={styles.metricLabel}>{label}</h2>
        {state === 'loading' ? (
          <Skeleton.Input active size="small" aria-label={`${label}加载中`} />
        ) : (
          <div className={styles.metricValue}>
            <strong>{displayValue}</strong>
            {state === 'ready' && unit ? <span className={styles.metricUnit}>{unit}</span> : null}
          </div>
        )}
        {description ? <div className={styles.metricDescription}>{description}</div> : null}
        {basis || asOf ? (
          <dl className={styles.metricMetadata}>
            {basis ? (
              <>
                <dt>口径</dt>
                <dd>{basis}</dd>
              </>
            ) : null}
            {asOf ? (
              <>
                <dt>截至</dt>
                <dd>{asOf}</dd>
              </>
            ) : null}
          </dl>
        ) : null}
      </Card>
    </section>
  );
}
