import {
  DatabaseZap,
  Send,
  ShieldCheck,
  Tags,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import type {
  DashboardActivity,
  DashboardActivityEventType,
} from "../../../features/dashboard/types";
import { PageState, StatusTag } from "../../../shared/ui";
import {
  DashboardSectionNotice,
  sectionLabel,
  sectionTone,
} from "./DashboardSectionNotice";
import styles from "../styles.module.css";

const eventPresentation: Readonly<
  Record<
    DashboardActivityEventType,
    Readonly<{
      Icon: LucideIcon;
      label: string;
    }>
  >
> = {
  UPLOAD_COMMITTED: { Icon: DatabaseZap, label: "上传已提交" },
  QC_COMPLETED: { Icon: ShieldCheck, label: "自动质检完成" },
  TAG_REVIEW_DECIDED: { Icon: Tags, label: "Tag 审核已决策" },
  DATASET_PUBLISHED: { Icon: Send, label: "数据集已发布" },
};

const sourceStateCopy: Readonly<Record<string, string>> = {
  RAW_COMMITTED: "原始数据已提交",
  COMMITTED: "已提交",
  PASS: "通过",
  RISK: "有风险",
  REJECT: "未通过",
  APPROVE: "已通过",
  NEEDS_REVISION: "需修改",
  PUBLISHED: "已发布",
};

function sourceStateLabel(sourceState: string): string {
  return sourceStateCopy[sourceState] ?? "状态未知";
}

function formatTime(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function DashboardActivityList({
  activity,
  onRetry,
}: Readonly<{
  activity: DashboardActivity;
  onRetry?: () => void;
}>) {
  return (
    <section
      className={`${styles.panel} ${styles.activityPanel}`}
      aria-labelledby="dashboard-activity-title"
    >
      <div className={styles.panelHeading}>
        <div>
          <h2 id="dashboard-activity-title">最近活动</h2>
          <p>来自当前项目与区域的持久业务事件</p>
        </div>
        <StatusTag
          status={activity.section.status}
          label={sectionLabel(activity.section.status)}
          known
          tone={sectionTone(activity.section.status)}
        />
      </div>

      <DashboardSectionNotice
        section={activity.section}
        label="最近活动"
        onRetry={onRetry}
      />
      {activity.items.length === 0 ? (
        <div className={styles.compactState}>
          <PageState state="empty" label="最近活动" title="当前时段暂无活动" />
        </div>
      ) : (
        <ol className={styles.activityList}>
          {activity.items.map((event) => {
            const { Icon, label } = eventPresentation[event.eventType];
            const content = (
              <>
                <strong>{event.title || label}</strong>
                <span>{event.summary}</span>
              </>
            );
            return (
              <li key={event.eventId}>
                <time dateTime={event.occurredAt}>
                  {formatTime(event.occurredAt, activity.timezone)}
                </time>
                <span className={styles.activityIcon} aria-hidden="true">
                  <Icon size={15} strokeWidth={1.8} />
                </span>
                <div>
                  {event.target.deep_link ? (
                    <Link to={event.target.deep_link}>{content}</Link>
                  ) : (
                    content
                  )}
                  <small data-source-state={event.sourceState}>
                    {label} · {sourceStateLabel(event.sourceState)}
                  </small>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
