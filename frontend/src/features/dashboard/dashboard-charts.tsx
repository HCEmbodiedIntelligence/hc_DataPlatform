import { useEffect, useRef } from 'react';
import type { DashboardActivity, DashboardSnapshot } from './types';

export function DashboardCharts(props: Readonly<{
  activity: DashboardActivity;
  snapshot: DashboardSnapshot;
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
        xAxis: { type: 'category', data: props.activity.buckets.map((bucket) => bucket.start.slice(11, 16)) },
        yAxis: { type: 'value', name: 'bytes' },
        series: [{ type: 'bar', name: '上传量', data: props.activity.buckets.map((bucket) => bucket.acceptedUniqueBytes.toString()) }],
      });
      storage.setOption({
        tooltip: { trigger: 'item' },
        legend: { bottom: 0 },
        series: [{
          type: 'pie',
          radius: ['42%', '70%'],
          data: props.snapshot.roles.map((role) => ({ name: role.role === 'UNKNOWN' ? '未知' : role.role, value: role.bytes.toString() })),
        }],
      });
      history.setOption({
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0 },
        xAxis: { type: 'category', data: props.snapshot.history.map((item) => item.month) },
        yAxis: { type: 'value', name: 'bytes' },
        series: [
          { type: 'line', name: 'STANDARD', data: props.snapshot.history.map((item) => item.standardBytes.toString()) },
          { type: 'line', name: 'IA', data: props.snapshot.history.map((item) => item.iaBytes.toString()) },
          { type: 'line', name: 'ARCHIVE', data: props.snapshot.history.map((item) => item.archiveBytes.toString()) },
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
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 16 }}>
      <section aria-labelledby="upload-trend-title" style={{ background: '#fff', border: '1px solid #d9e2e1', borderRadius: 8, padding: 16 }}>
        <h2 id="upload-trend-title">上传趋势</h2>
        <div ref={uploadRef} style={{ height: 260 }} role="img" aria-label="按时间分桶的上传字节趋势图" />
        <details><summary>查看上传趋势数据表</summary><ul>{props.activity.buckets.map((bucket) => <li key={bucket.start}>{bucket.start}：{bucket.acceptedUniqueBytes.toString()} bytes</li>)}</ul></details>
      </section>
      <section aria-labelledby="storage-composition-title" style={{ background: '#fff', border: '1px solid #d9e2e1', borderRadius: 8, padding: 16 }}>
        <h2 id="storage-composition-title">存储构成</h2>
        <div ref={storageRef} style={{ height: 260 }} role="img" aria-label="按对象角色划分的物理容量环图" />
        <details><summary>查看存储构成数据表</summary><ul>{props.snapshot.roles.map((role) => <li key={role.wireRole}>{role.role}：{role.bytes.toString()} bytes</li>)}</ul></details>
      </section>
      <section aria-labelledby="episode-availability-title" style={{ background: '#fff', border: '1px solid #d9e2e1', borderRadius: 8, padding: 16 }}>
        <h2 id="episode-availability-title">Episode 可用性</h2>
        <ol aria-label="Episode 可用性漏斗">
          <li>已上传：{props.snapshot.episodes.uploadedCount.toLocaleString('zh-CN')}</li>
          <li>已校验：{props.snapshot.episodes.validatedCount.toLocaleString('zh-CN')}</li>
          <li>可查看：{props.snapshot.episodes.viewableCount.toLocaleString('zh-CN')}</li>
        </ol>
      </section>
      <section aria-labelledby="storage-trend-title" style={{ background: '#fff', border: '1px solid #d9e2e1', borderRadius: 8, padding: 16 }}>
        <h2 id="storage-trend-title">月度存储趋势</h2>
        <div ref={historyRef} style={{ height: 260 }} role="img" aria-label="按存储层级划分的月度物理容量趋势图" />
        <details><summary>查看月度存储数据表</summary><ul>{props.snapshot.history.map((item) => <li key={item.month}>{item.month}：{item.dataPhysicalBytes.toString()} bytes</li>)}</ul></details>
      </section>
    </div>
  );
}

export default DashboardCharts;
