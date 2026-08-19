import { Alert } from "antd";
import type {
  DashboardSection,
  DashboardSectionStatus,
} from "../../../features/dashboard/types";
import type { StatusTone } from "../../../shared/ui/state/contracts";

const statusCopy: Readonly<Record<DashboardSectionStatus, string>> = {
  READY: "数据已就绪",
  EMPTY: "当前范围暂无数据",
  PARTIAL: "仅展示已确认返回的数据",
  STALE: "当前数据可能已过期",
  ERROR: "此区域加载失败",
  BLOCKED: "此区域被产品或事实合同阻断",
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
  const description = section.error?.message ?? statusCopy[section.status];
  return (
    <Alert
      className={compact ? "dashboard-section-notice-compact" : undefined}
      data-dashboard-section-status={section.status}
      title={`${label} · ${section.status}`}
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
