import { Card } from 'antd';
import { Activity, GitBranch, RadioTower } from 'lucide-react';
import type { ReactNode } from 'react';
import type { DashboardActivity, DashboardSnapshot } from './types';
import styles from './dashboard-charts.module.css';

function time(value: string, timezone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));
}

export function DashboardCharts(props: Readonly<{
  activity: DashboardActivity;
  snapshot: DashboardSnapshot;
  coverage: ReactNode;
}>) {
  const published = props.snapshot.signalPipeline.publishedRegion;
  return (
    <section className={styles.grid} aria-label="工作台事实">
      <Card title={<span><Activity size={18} aria-hidden="true" /> 业务事件流</span>}>
        {props.activity.items.length ? (
          <ol>
            {props.activity.items.map((event) => (
              <li key={event.eventId}>
                <time dateTime={event.occurredAt}>{time(event.occurredAt, props.activity.timezone)}</time>
                {' · '}{event.eventType} · {event.title} — {event.summary}
              </li>
            ))}
          </ol>
        ) : <p>当前窗口没有授权可见的业务事件。</p>}
      </Card>
      <Card title={<span><RadioTower size={18} aria-hidden="true" /> 固定信号阶段</span>}>
        <ol>{props.snapshot.signalPipeline.stages.map((stage) => <li key={stage}>{stage}</li>)}</ol>
      </Card>
      <Card title={<span><GitBranch size={18} aria-hidden="true" /> 发布血缘</span>}>
        <p>状态 {published.status}</p>
        <p>血缘 {published.lineageCount ?? '未知'} · 发布 {published.publicationCount ?? '未知'} · 未解析历史 {published.unresolvedHistoryCount}</p>
      </Card>
      <Card title="覆盖率合同">{props.coverage}</Card>
    </section>
  );
}

export default DashboardCharts;
