import { Card } from 'antd';
import {
  Activity,
  Database,
  GitBranch,
  Grid3X3,
  Layers3,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';
import type { DashboardActivity, DashboardSnapshot } from './types';
import styles from './dashboard-charts.module.css';

function compactBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;
  let amount = value;
  let unitIndex = 0;
  while (Math.abs(amount) >= 1_024 && unitIndex < units.length - 1) {
    amount /= 1_024;
    unitIndex += 1;
  }
  return `${amount.toLocaleString('zh-CN', { maximumFractionDigits: amount >= 10 ? 0 : 1 })} ${units[unitIndex]}`;
}

function readableBytes(value: bigint): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;
  let unitIndex = 0;
  let divisor = 1n;
  while (value >= divisor * 1_024n && unitIndex < units.length - 1) {
    divisor *= 1_024n;
    unitIndex += 1;
  }
  if (unitIndex === 0) return `${value.toLocaleString('zh-CN')} B`;

  const whole = value / divisor;
  if (whole >= 10n) {
    const rounded = (value + divisor / 2n) / divisor;
    return `${rounded.toLocaleString('zh-CN')} ${units[unitIndex]}`;
  }

  const tenths = (value * 10n + divisor / 2n) / divisor;
  const fraction = tenths % 10n;
  return `${tenths / 10n}${fraction === 0n ? '' : `.${fraction}`} ${units[unitIndex]}`;
}

function readableDateTime(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function readableTime(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function ChartCardTitle({
  eyebrow,
  title,
  description,
  icon: Icon,
}: Readonly<{
  eyebrow: string;
  title: string;
  description: string;
  icon: LucideIcon;
}>) {
  return (
    <div className={styles.cardTitle}>
      <span className={styles.cardTitleIcon} aria-hidden="true">
        <Icon size={19} strokeWidth={1.8} />
      </span>
      <span className={styles.cardTitleCopy}>
        <span className={styles.cardEyebrow}>{eyebrow}</span>
        <h2>{title}</h2>
        <span className={styles.cardDescription}>{description}</span>
      </span>
    </div>
  );
}

export function DashboardCharts(props: Readonly<{
  activity: DashboardActivity;
  snapshot: DashboardSnapshot;
  coverage: ReactNode;
}>) {
  const uploadRef = useRef<HTMLDivElement>(null);
  const storageRef = useRef<HTMLDivElement>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let uploadChart: { dispose(): void; resize(): void } | undefined;
    let storageChart: { dispose(): void; resize(): void } | undefined;
    let historyChart: { dispose(): void; resize(): void } | undefined;
    const observers: ResizeObserver[] = [];
    const uploadValues = props.activity.buckets.map((bucket) => Number(bucket.acceptedUniqueBytes));
    const uploadMaximum = Math.max(...uploadValues, 1);
    const failureValues = props.activity.buckets.map((bucket) => Number(bucket.failedCount));
    const failureMaximum = Math.max(...failureValues, 1);

    void (async () => {
      const core = await import('echarts/core');
      const charts = await import('echarts/charts');
      const components = await import('echarts/components');
      const renderers = await import('echarts/renderers');
      if (disposed || !uploadRef.current || !storageRef.current || !historyRef.current) return;
      core.use([
        charts.BarChart,
        charts.LineChart,
        charts.PieChart,
        components.GridComponent,
        components.LegendComponent,
        components.TooltipComponent,
        components.DatasetComponent,
        renderers.CanvasRenderer,
      ]);
      const upload = core.init(uploadRef.current);
      const storage = core.init(storageRef.current);
      const history = core.init(historyRef.current);
      uploadChart = upload;
      storageChart = storage;
      historyChart = history;
      upload.setOption({
        tooltip: { trigger: 'axis' },
        grid: { left: 72, right: 44, top: 18, bottom: 28, containLabel: false },
        xAxis: {
          type: 'category',
          boundaryGap: props.activity.buckets.length === 1,
          data: props.activity.buckets.map((bucket) => readableTime(bucket.start, props.activity.timezone)),
          axisTick: { show: false },
          axisLabel: { color: '#676b80', fontSize: 10, hideOverlap: true },
        },
        yAxis: [
          {
            type: 'value',
            min: 0,
            max: Math.ceil(uploadMaximum * 1.15),
            splitNumber: 3,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: {
              color: '#676b80',
              fontSize: 10,
              formatter: (value: number) => compactBytes(value),
              hideOverlap: true,
            },
            splitLine: { lineStyle: { color: '#e7e9f2', type: 'dashed' } },
          },
          {
            type: 'value',
            name: '失败',
            min: 0,
            max: failureMaximum,
            splitNumber: Math.min(2, failureMaximum),
            minInterval: 1,
            nameTextStyle: { color: '#676b80', fontSize: 10, align: 'right' },
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: '#676b80', fontSize: 10, hideOverlap: true },
            splitLine: { show: false },
          },
        ],
        color: ['#5965d8', '#ef4444'],
        series: [
          {
            type: 'line',
            name: '上传吞吐量',
            smooth: true,
            showSymbol: true,
            symbolSize: props.activity.buckets.length === 1 ? 10 : 6,
            lineStyle: { width: 2 },
            areaStyle: { opacity: 0.09 },
            data: uploadValues,
          },
          {
            type: 'line',
            name: '失败',
            yAxisIndex: 1,
            symbol: 'cross',
            symbolSize: 9,
            lineStyle: { opacity: 0 },
            data: failureValues,
          },
        ],
      });
      storage.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: 72, right: 20, top: 6, bottom: 22 },
        xAxis: { type: 'value', axisLabel: { formatter: (value: number) => compactBytes(value) } },
        yAxis: {
          type: 'category',
          inverse: true,
          data: props.snapshot.roles.map((role) => role.role === 'UNKNOWN' ? '未知' : role.role),
        },
        color: ['#5965d8'],
        series: [{
          type: 'bar',
          name: '物理容量',
          barMaxWidth: 18,
          data: props.snapshot.roles.map((role) => Number(role.bytes)),
        }],
      });
      history.setOption({
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0 },
        grid: { left: 54, right: 24, top: 12, bottom: 42 },
        xAxis: { type: 'category', data: props.snapshot.history.map((item) => item.month) },
        yAxis: { type: 'value', axisLabel: { formatter: (value: number) => compactBytes(value) } },
        color: ['#5965d8', '#f59e0b', '#3b82f6', '#9b72d8'],
        series: [
          { type: 'bar', stack: 'storage', name: 'STANDARD', data: props.snapshot.history.map((item) => Number(item.standardBytes)) },
          { type: 'bar', stack: 'storage', name: 'IA', data: props.snapshot.history.map((item) => Number(item.iaBytes)) },
          { type: 'bar', stack: 'storage', name: 'ARCHIVE', data: props.snapshot.history.map((item) => Number(item.archiveBytes)) },
          { type: 'line', name: '总量', data: props.snapshot.history.map((item) => Number(item.dataPhysicalBytes)) },
        ],
      });
      for (const [element, chart] of [[uploadRef.current, upload], [storageRef.current, storage], [historyRef.current, history]] as const) {
        const observer = new ResizeObserver(() => chart.resize());
        observer.observe(element);
        observers.push(observer);
      }
    })();

    return () => {
      disposed = true;
      observers.forEach((observer) => observer.disconnect());
      uploadChart?.dispose();
      storageChart?.dispose();
      historyChart?.dispose();
    };
  }, [props.activity, props.snapshot]);

  return (
    <div className={styles.grid}>
      <Card
        className={styles.uploadCard}
        size="small"
        title={(
          <ChartCardTitle
            eyebrow="INGEST"
            title="24 小时上传吞吐量与失败情况"
            description="输入速率与异常信号"
            icon={Activity}
          />
        )}
      >
        <div ref={uploadRef} className={styles.chart} role="img" aria-label="按时间分桶的上传字节趋势图" />
        <details className={styles.dataDisclosure}>
          <summary>查看上传趋势数据表</summary>
          <ul>
            {props.activity.buckets.map((bucket) => (
              <li key={bucket.start}>
                <time dateTime={bucket.start}>{readableDateTime(bucket.start, props.activity.timezone)}</time>
                {'：上传 '}{readableBytes(bucket.acceptedUniqueBytes)}，失败 {bucket.failedCount.toLocaleString('zh-CN')} 次
              </li>
            ))}
          </ul>
        </details>
      </Card>
      <Card
        className={styles.storageCard}
        size="small"
        title={(
          <ChartCardTitle
            eyebrow="STORAGE"
            title="存储构成"
            description="按对象角色查看物理占用"
            icon={Database}
          />
        )}
      >
        <div ref={storageRef} className={styles.chart} role="img" aria-label="按对象角色划分的物理容量横向条形图" />
        <details className={styles.dataDisclosure}><summary>查看存储构成数据表</summary><ul>{props.snapshot.roles.map((role) => <li key={role.wireRole}>{role.role}：{readableBytes(role.bytes)}</li>)}</ul></details>
      </Card>
      <Card
        className={styles.episodeCard}
        size="small"
        title={(
          <ChartCardTitle
            eyebrow="EPISODE"
            title="Episode 可用性"
            description="从接收到可查看的转化"
            icon={GitBranch}
          />
        )}
      >
        <ol className={styles.funnel} aria-label="Episode 可用性漏斗">
          <li>
            <span className={styles.stepIndex}>01</span>
            <span className={styles.stepCopy}><span>已上传</span><small>RAW RECEIVED</small></span>
            <strong>{props.snapshot.episodes.uploadedCount.toLocaleString('zh-CN')}</strong>
          </li>
          <li>
            <span className={styles.stepIndex}>02</span>
            <span className={styles.stepCopy}><span>已校验</span><small>VALIDATED</small></span>
            <strong>{props.snapshot.episodes.validatedCount.toLocaleString('zh-CN')}</strong>
          </li>
          <li>
            <span className={styles.stepIndex}>03</span>
            <span className={styles.stepCopy}><span>可查看</span><small>VIEWABLE</small></span>
            <strong>{props.snapshot.episodes.viewableCount.toLocaleString('zh-CN')}</strong>
          </li>
        </ol>
      </Card>
      <Card
        className={styles.coverageCard}
        size="small"
        title={(
          <ChartCardTitle
            eyebrow="COVERAGE"
            title="机器人与任务覆盖矩阵"
            description="机器人组 × 任务矩阵"
            icon={Grid3X3}
          />
        )}
      >
        {props.coverage}
      </Card>
      <Card
        className={styles.historyCard}
        size="small"
        title={(
          <ChartCardTitle
            eyebrow="HISTORY"
            title="月度存储增长与分层构成"
            description="容量增长与存储层级迁移"
            icon={Layers3}
          />
        )}
      >
        <div ref={historyRef} className={styles.chart} role="img" aria-label="按存储层级划分的月度物理容量趋势图" />
        <details className={styles.dataDisclosure}><summary>查看月度存储数据表</summary><ul>{props.snapshot.history.map((item) => <li key={item.month}>{item.month}：{readableBytes(item.dataPhysicalBytes)}</li>)}</ul></details>
      </Card>
    </div>
  );
}

export default DashboardCharts;
