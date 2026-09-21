import type { StatusTone } from "../../shared/ui";
import type {
  AccessDecision,
  AccessRequestRow,
  AccessRequestStatus,
} from "./contracts";

const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Shanghai",
});

const statusPresentation: Readonly<
  Record<
    AccessRequestStatus,
    { readonly label: string; readonly tone: StatusTone }
  >
> = {
  PENDING: { label: "待审批", tone: "warning" },
  APPROVED: { label: "已批准", tone: "success" },
  REJECTED: { label: "已拒绝", tone: "danger" },
  WITHDRAWN: { label: "已撤回", tone: "neutral" },
  REVOKED: { label: "已撤销", tone: "danger" },
};

const capabilityLabels: Readonly<Record<string, string>> = {
  "access.manage": "项目访问管理",
  "dataset.read": "数据集只读",
  "dataset.create": "创建数据集",
  "dataset.update": "编辑数据集",
  "dataset_version.publish": "数据集发布",
  "upload.manage": "数据全流程（上传、标注、审核、发布、导出）",
  "ingest.import": "数据导入",
  "annotation.edit": "数据标注",
  "annotation.review": "标注审核",
  "data_schema.publish": "数据结构管理",
  "dashboard.read": "工作台只读",
};

const elevatedImpact: Readonly<Record<string, string>> = {
  "upload.manage":
    "批准后，同一账号可在当前项目内完成上传、标注、自审、发布和导出下载。",
  "access.manage": "批准后可管理并审批当前项目的访问申请。",
  "dataset_version.publish": "批准后可冻结并发布当前项目的数据集版本。",
  "data_schema.publish": "批准后可变更当前项目的数据结构。",
};

export function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "时间格式无效"
    : dateTimeFormatter.format(parsed);
}

export function shortIdentity(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 9)}…${value.slice(-6)}`;
}

export function statusLabel(status: AccessRequestStatus): string {
  return statusPresentation[status].label;
}

export function statusTone(status: AccessRequestStatus): StatusTone {
  return statusPresentation[status].tone;
}

export function capabilityLabel(key: string): string {
  return capabilityLabels[key] ?? key;
}

export function accessRequestTitle(row: AccessRequestRow): string {
  return row.kind === "membership" ? "申请加入项目" : "申请项目权限";
}

export function accessRequestSummary(row: AccessRequestRow): string {
  if (row.kind === "membership") return row.reason || "未填写申请原因";
  if (row.capabilityKeys.length === 0) return "未列出申请能力";
  return row.capabilityKeys.map(capabilityLabel).join("、");
}

export function elevatedImpactNotes(row: AccessRequestRow): readonly string[] {
  return row.capabilityKeys.flatMap((key) =>
    elevatedImpact[key] ? [elevatedImpact[key]] : [],
  );
}

export function availableDecisions(
  row: AccessRequestRow,
  canManage: boolean,
  _principalId: string | null,
): readonly AccessDecision[] {
  if (row.status === "PENDING") {
    return canManage ? ["approve", "reject"] : [];
  }
  if (row.status === "APPROVED" && canManage) return ["revoke"];
  return [];
}

export function decisionLabel(action: AccessDecision): string {
  switch (action) {
    case "approve":
      return "批准申请";
    case "reject":
      return "拒绝申请";
    case "revoke":
      return "撤销授权";
  }
}
