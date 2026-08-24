import { Alert } from "antd";
import type {
  DashboardSection,
  DashboardSectionStatus,
} from "../../../features/dashboard/types";
import type { StatusTone } from "../../../shared/ui/state/contracts";

const statusCopy: Readonly<Record<DashboardSectionStatus, string>> = {
  READY: "正常",
  EMPTY: "暂无数据",
  PARTIAL: "数据不完整",
  STALE: "数据可能已过期",
  ERROR: "加载失败",
  BLOCKED: "暂时无法计算",
};

const blockerCopy: Readonly<Record<string, string>> = {
  P01_SIGNAL_FORMULA_UNCONFIRMED:
    "信号轨道的统计规则还没配置完成，因此暂时不能显示各阶段数量。",
  P01_COVERAGE_DENOMINATOR_MISSING:
    "缺少采集计划、机器人分组、任务目录或目标总量，因此暂时无法计算覆盖率。",
  COVERAGE_PRODUCT_DECISION_REQUIRED:
    "覆盖率的统计规则和目标总量还没配置完成，因此暂时无法计算。",
};

export function sectionTone(status: DashboardSectionStatus): StatusTone {
  if (status === "READY") return "success";
  if (status === "ERROR") return "danger";
  if (status === "PARTIAL" || status === "STALE" || status === "BLOCKED")
    return "warning";
  return "neutral";
}

export function sectionLabel(status: DashboardSectionStatus): string {
  return statusCopy[status];
}

export function DashboardSectionNotice({
  section,
  label,
  compact = false,
  onRetry,
}: Readonly<{
  section: DashboardSection;
  label: string;
  compact?: boolean;
  onRetry?: () => void;
}>) {
  if (section.status === "READY" || section.status === "EMPTY") return null;
  const isError = section.status === "ERROR";
  const description = section.error
    ? (blockerCopy[section.error.code] ?? section.error.message)
    : statusCopy[section.status];
  return (
    <Alert
      className={compact ? "dashboard-section-notice-compact" : undefined}
      data-dashboard-section-status={section.status}
      title={`${label} · ${sectionLabel(section.status)}`}
      description={description}
      type={isError ? "error" : "warning"}
      showIcon
      action={
        onRetry && (isError || section.error?.retryable) ? (
          <button type="button" onClick={onRetry}>
            重试
          </button>
        ) : undefined
      }
    />
  );
}
