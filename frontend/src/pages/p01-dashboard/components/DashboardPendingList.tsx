import {
  ArrowUpRight,
  CloudUpload,
  Send,
  ShieldAlert,
  Tags,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import type {
  DashboardPendingItem,
  DashboardPendingKind,
} from "../../../features/dashboard/types";
import { StatusTag } from "../../../shared/ui";
import styles from "../styles.module.css";

const pendingPresentation: Readonly<
  Record<
    DashboardPendingKind,
    Readonly<{
      title: string;
      action: string;
      description: string;
      Icon: LucideIcon;
    }>
  >
> = {
  UPLOAD_FAILED: {
    title: "上传失败",
    action: "查看上传记录",
    description: "上传会话需要重试或排查失败分片",
    Icon: CloudUpload,
  },
  QC_ANOMALY: {
    title: "自动质检异常",
    action: "查看 Raw 诊断",
    description: "自动质检未通过的样本需要查看 Raw 证据",
    Icon: ShieldAlert,
  },
  TAG_REVIEW_PENDING: {
    title: "待审核",
    action: "进入审核",
    description: "Tag 修订已提交，等待有权限的审核人处理",
    Icon: Tags,
  },
  PUBLICATION_PENDING: {
    title: "待冻结发布",
    action: "进入发布",
    description: "审核已通过，等待冻结并进入发布流程",
    Icon: Send,
  },
};

const severityCopy: Readonly<
  Record<DashboardPendingItem["severity"], string>
> = {
  CRITICAL: "紧急",
  HIGH: "高",
  MEDIUM: "中",
  LOW: "低",
};

const sourceStateCopy: Readonly<Record<string, string>> = {
  FAILED: "失败",
  RISK: "有风险",
  REJECT: "未通过",
  SUBMITTED: "已提交",
  APPROVED: "已通过",
};

function sourceStateLabel(sourceState: string): string {
  return sourceStateCopy[sourceState] ?? "状态未知";
}

function severityTone(severity: DashboardPendingItem["severity"]) {
  return severity === "CRITICAL"
    ? "danger"
    : severity === "HIGH"
      ? "warning"
      : "neutral";
}

export function DashboardPendingList({
  items,
}: Readonly<{ items: readonly DashboardPendingItem[] }>) {
  return (
    <ul className={styles.pendingList} aria-label="授权范围内的待办事项">
      {items.map((item) => {
        const { title, action, description, Icon } =
          pendingPresentation[item.kind];
        return (
          <li key={item.itemId}>
            <span
              className={styles.pendingIcon}
              data-kind={item.kind}
              aria-hidden="true"
            >
              <Icon size={21} strokeWidth={1.8} />
            </span>
            <div className={styles.pendingCopy}>
              <div className={styles.pendingTitle}>
                <strong>{title}</strong>
                <StatusTag
                  status={item.severity}
                  label={severityCopy[item.severity]}
                  known
                  tone={severityTone(item.severity)}
                />
              </div>
              <span>{description}</span>
              <small
                title={item.target.resource_id}
                data-source-state={item.sourceState}
              >
                {sourceStateLabel(item.sourceState)} · {item.target.resource_id}
              </small>
            </div>
            {item.target.deep_link ? (
              <Link className={styles.pendingAction} to={item.target.deep_link}>
                {action}
                <ArrowUpRight aria-hidden="true" size={14} />
              </Link>
            ) : (
              <span className={styles.pendingUnavailable}>目标暂不可用</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
