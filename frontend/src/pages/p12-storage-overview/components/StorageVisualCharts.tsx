import { Info } from 'lucide-react';
import type { CSSProperties } from 'react';
import type { StorageOverview } from '../../../features/storage-overview/types';
import {
  displayDecimalMetric,
  formatByteString,
} from '../../../features/storage-overview/metrics-contract';
import type { Int64String } from '../../../shared/lib/bigint-string';
import styles from '../styles.module.css';

const STORAGE_COLORS: Record<string, string> = {
  STANDARD: '#078d7d',
  IA: '#f59e0b',
  ARCHIVE: '#4f7dd9',
};

function asCapacity(value: string | null): bigint {
  if (value === null) return 0n;
  try {
    return BigInt(value);
  } catch {
    return 0n;
  }
}

function percent(value: bigint, total: bigint): number {
  if (total <= 0n) return 0;
  return Number((value * 10000n) / total) / 100;
}

function formatCapacity(value: bigint): string {
  return formatByteString(value.toString() as Int64String);
}

export function StorageVisualCharts({ overview }: Readonly<{ overview: StorageOverview }>) {
  const monthly = [...new Set(overview.growth.map((item) => item.month))].map((month) => ({
    month,
    physicalBytes: overview.growth.reduce(
      (total, item) => total + (item.month === month ? asCapacity(item.physicalBytes) : 0n),
      0n,
    ),
  }));
  const highestMonthly = monthly.reduce(
    (highest, item) => (item.physicalBytes > highest ? item.physicalBytes : highest),
    0n,
  );
  const classes = overview.storageClasses.map((item) => ({
    ...item,
    physical: asCapacity(item.physicalBytes),
  }));
  const classTotal = classes.reduce((total, item) => total + item.physical, 0n);
  let cursor = 0;
  const gradient = classes
    .map((item) => {
      const end = cursor + percent(item.physical, classTotal);
      const range = `${STORAGE_COLORS[item.storageClass] ?? '#94a3b8'} ${cursor}% ${end}%`;
      cursor = end;
      return range;
    })
    .join(', ');
  const logical =
    overview.totals.logicalReferencedBytes.state === 'KNOWN'
      ? formatByteString(overview.totals.logicalReferencedBytes.value)
      : overview.totals.logicalReferencedBytes.state;
  const actual =
    overview.totals.actualOssPhysicalBytes.state === 'KNOWN'
      ? formatByteString(overview.totals.actualOssPhysicalBytes.value)
      : overview.totals.actualOssPhysicalBytes.state;
  const reuse = displayDecimalMetric(overview.totals.reuseRate);

  return (
    <section className={styles.storageVisualCharts} aria-label="存储概览图表">
      <article className={styles.storageChartCard}>
        <header className={styles.storageChartHeader}>
          <div>
            <h2>最近容量趋势</h2>
            <p>服务端快照中的对象角色物理量</p>
          </div>
          <span>TB</span>
        </header>
        <div
          className={styles.trendVisual}
          role="img"
          aria-label="按对象角色划分的月度物理容量趋势图"
        >
          <div className={styles.trendGrid} aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
          </div>
          <div className={styles.trendColumns}>
            {monthly.map((item) => {
              const height =
                highestMonthly > 0n
                  ? Math.max(12, Math.round(percent(item.physicalBytes, highestMonthly)))
                  : 0;
              return (
                <div className={styles.trendColumn} key={item.month}>
                  <strong>{formatCapacity(item.physicalBytes)}</strong>
                  <span className={styles.trendBar} style={{ height: `${height}%` }} />
                  <time dateTime={`${item.month}-01`}>{item.month}</time>
                </div>
              );
            })}
          </div>
        </div>
        <footer className={styles.chartLegend}>
          {overview.roles.map((role, index) => (
            <span key={role.role}>
              <i
                style={{
                  background: ['#078d7d', '#29a98b', '#65b85b', '#f59e0b', '#7367d8'][index % 5],
                }}
              />
              {role.role}
            </span>
          ))}
        </footer>
      </article>

      <article className={styles.storageChartCard}>
        <header className={styles.storageChartHeader}>
          <div>
            <h2>存储层级分布</h2>
            <p>同一 Inventory 快照</p>
          </div>
          <Info size={16} aria-label="分层容量说明" />
        </header>
        <div className={styles.distributionVisual}>
          <div
            className={styles.storageDonut}
            role="img"
            aria-label="存储层级物理容量分布图"
            style={{ '--storage-donut': gradient || '#e6ecea 0% 100%' } as CSSProperties}
          >
            <span>
              物理容量<strong>{formatCapacity(classTotal)}</strong>
            </span>
          </div>
          <dl className={styles.distributionLegend}>
            {classes.map((item) => (
              <div key={item.storageClass}>
                <dt>
                  <i style={{ background: STORAGE_COLORS[item.storageClass] ?? '#94a3b8' }} />
                  {item.storageClass}
                </dt>
                <dd>
                  {percent(item.physical, classTotal)}% · {formatCapacity(item.physical)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <dl className={styles.storageFacts}>
          <div>
            <dt>逻辑数据量</dt>
            <dd>{logical}</dd>
          </div>
          <div>
            <dt>物理存储</dt>
            <dd>{actual}</dd>
          </div>
          <div>
            <dt>对象复用率</dt>
            <dd>{reuse}</dd>
          </div>
        </dl>
      </article>
    </section>
  );
}
