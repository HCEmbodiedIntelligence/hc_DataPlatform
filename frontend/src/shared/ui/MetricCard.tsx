import type { ReactNode } from 'react';
import { SkeletonBlock } from './SkeletonBlock';

export interface MetricCardProps {
  label: string;
  value?: ReactNode;
  detail?: ReactNode;
  loading?: boolean;
}

export function MetricCard({ label, value, detail, loading = false }: MetricCardProps) {
  return (
    <section className="metric-card" aria-label={label}>
      <h2>{label}</h2>
      {loading ? <SkeletonBlock width="60%" height="2rem" label={`${label}加载中`} /> : <div className="metric-card__value">{value ?? '—'}</div>}
      {detail ? <div className="metric-card__detail">{detail}</div> : null}
    </section>
  );
}
