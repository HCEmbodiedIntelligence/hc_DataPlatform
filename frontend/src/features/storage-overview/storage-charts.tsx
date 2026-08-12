import { Card } from 'antd';
import { useEffect, useRef } from 'react';
import type { StorageOverview } from './types';
import styles from './storage-charts.module.css';

export function StorageCharts({ overview }: Readonly<{ overview: StorageOverview }>) {
  const tierRef = useRef<HTMLDivElement>(null);
  const trendRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let tier: { dispose(): void; resize(): void } | undefined;
    let trend: { dispose(): void; resize(): void } | undefined;
    const observers: ResizeObserver[] = [];
    void (async () => {
      const core = await import('echarts/core');
      const charts = await import('echarts/charts');
      const components = await import('echarts/components');
      const renderers = await import('echarts/renderers');
      if (disposed || !tierRef.current || !trendRef.current) return;
      core.use([charts.PieChart, charts.LineChart, components.GridComponent, components.LegendComponent, components.TooltipComponent, renderers.CanvasRenderer]);
      const tierChart = core.init(tierRef.current);
      const trendChart = core.init(trendRef.current);
      tier = tierChart;
      trend = trendChart;
      tierChart.setOption({
        tooltip: { trigger: 'item' },
        legend: { bottom: 0 },
        series: [{ type: 'pie', radius: ['40%', '70%'], data: overview.storageClasses.filter((item) => item.physicalBytes !== null).map((item) => ({ name: item.storageClass, value: item.physicalBytes! })) }],
      });
      const months = [...new Set(overview.growth.map((item) => item.month))];
      trendChart.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: months },
        yAxis: { type: 'value', name: 'bytes' },
        series: overview.roles.map((role) => ({
          type: 'line',
          name: role.role,
          data: months.map((month) => overview.growth.find((item) => item.month === month && item.role === role.role)?.physicalBytes ?? null),
        })),
      });
      for (const [element, chart] of [[tierRef.current, tierChart], [trendRef.current, trendChart]] as const) {
        const observer = new ResizeObserver(() => chart.resize());
        observer.observe(element);
        observers.push(observer);
      }
    })();
    return () => {
      disposed = true;
      observers.forEach((observer) => observer.disconnect());
      tier?.dispose();
      trend?.dispose();
    };
  }, [overview]);

  return (
    <div className={styles.grid}>
      <Card size="small" title={<h2>存储层级分布</h2>}>
        <div ref={tierRef} role="img" aria-label="存储层级物理容量分布图" className={styles.chart} />
        <details className={styles.dataDisclosure}>
          <summary>查看分布数据</summary>
          <ul>{overview.storageClasses.map((item) => <li key={item.storageClass}>{item.storageClass}：{item.physicalBytes ?? '无数据'}</li>)}</ul>
        </details>
      </Card>
      <Card size="small" title={<h2>容量趋势</h2>}>
        <div ref={trendRef} role="img" aria-label="按对象角色划分的月度物理容量趋势图" className={styles.chart} />
        <details className={styles.dataDisclosure}>
          <summary>查看趋势数据</summary>
          <ul>{overview.growth.map((item) => <li key={`${item.month}-${item.role}`}>{item.month} / {item.role}：{item.physicalBytes ?? item.completeness}</li>)}</ul>
        </details>
      </Card>
    </div>
  );
}

export default StorageCharts;
