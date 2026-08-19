import { Activity, GitBranch, ListTodo, RadioTower } from "lucide-react";
import type {
  DashboardActivity,
  DashboardPendingPage,
  DashboardSnapshot,
} from "../../../features/dashboard/types";
import { UiMetricCard, type MetricState } from "../../../shared/ui";
import styles from "../styles.module.css";

export function DashboardSummaryStrip({
  activity,
  snapshot,
  pending,
  activityState,
  snapshotState,
  pendingState,
}: Readonly<{
  activity?: DashboardActivity;
  snapshot?: DashboardSnapshot;
  pending?: DashboardPendingPage;
  activityState: MetricState;
  snapshotState: MetricState;
  pendingState: MetricState;
}>) {
  const published = snapshot?.signalPipeline.publishedRegion;
  return (
    <section className={styles.summaryGrid} aria-label="关键事实">
      <UiMetricCard
        eyebrow="PIPELINE"
        icon={<RadioTower size={19} />}
        label="信号链状态"
        value={snapshot?.signalPipeline.status}
        state={snapshotState}
        tone="primary"
      />
      <UiMetricCard
        eyebrow="EVENTS"
        icon={<Activity size={19} />}
        label="业务事件"
        value={activity ? String(activity.items.length) : undefined}
        state={activityState}
        tone="info"
      />
      <UiMetricCard
        eyebrow="PENDING"
        icon={<ListTodo size={19} />}
        label="授权待办"
        value={pending ? String(pending.items.length) : undefined}
        state={pendingState}
        tone="warning"
      />
      <UiMetricCard
        eyebrow="LINEAGE"
        icon={<GitBranch size={19} />}
        label="发布血缘"
        value={
          published?.lineageCount == null
            ? undefined
            : String(published.lineageCount)
        }
        state={snapshotState}
        tone="neutral"
      />
    </section>
  );
}
