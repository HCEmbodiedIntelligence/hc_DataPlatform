import { grantLabel } from "../../shared/auth/project-grants";
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
  "access.read": "查看项目授权",
  "access.role.customize": "自定义角色",
  "annotation_task.read": "查看标注任务及模型标注指引",
  "annotation.save": "保存标注与运行模型预标注",
  "annotation.submit": "提交标注",
  "annotation.read": "查看标注",
  "annotation_set.read": "查看标注集",
  "annotation_draft.edit": "编辑标注草稿",
  "annotation_task.assign": "分派标注任务",
  "annotation_task.claim": "领取标注任务",
  "annotation_task.create": "创建标注任务",
  "annotation_task.rebase": "更新标注任务基准版本",
  "export.read": "查看导出任务",
  "export.create": "创建导出任务",
  "export.download": "下载导出交付包",
  "teleoperation.connect": "远程遥操作 · 连接指定机器人",
  "training.create": "启动模型训练",
  "training.read": "查看模型训练资源与任务",
  "dataset.read": "数据集只读",
  "dataset.create": "创建数据集",
  "dataset.update": "编辑数据集",
  "dataset_version.publish": "数据集发布",
  "upload.manage": "上传与导入",
  "ingest.import": "数据导入",
  "annotation.edit": "数据标注",
  "annotation.review": "标注审核",
  "data_schema.publish": "数据结构管理",
  "dashboard.read": "工作台只读",
};

const elevatedImpact: Readonly<Record<string, string>> = {
  "upload.manage":
    "批准后可在当前项目上传数据。",
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

const subjects: Record<string, string> = {
  audit: "审计记录", calibration: "标定", "calibration.availability": "标定可用性",
  "calibration.report": "标定报告", "calibration.source": "标定源文件", cleaning: "清洗任务",
  collection_task: "采集任务", data_schema: "数据结构", dataset_version: "数据版本",
  manual_issue: "质量问题", robot: "机器人", robot_component: "机器人部件",
  robot_model: "机器人模型", "robot_model.asset": "机器人模型资产",
  "robot_model.validation_report": "模型验证报告", robot_model_binding: "机器人模型绑定",
  "storage.cost": "存储成本", "storage.inventory": "存储清单", "storage.lifecycle": "存储策略",
  "storage.multipart": "分片上传", "storage.object": "存储对象", "storage.overview": "存储概况",
  "storage.restore": "存储恢复", quality: "质量记录", upload: "上传记录", ingest_source: "数据源",
};
const actions: Record<string, string> = {
  read: "查看", manage: "管理", create: "创建", update: "更新", disable: "停用", edit: "编辑",
  validate: "校验", publish: "发布", download: "下载", export: "导出", change_mount: "修改挂载",
  refresh: "刷新", simulate: "模拟", abort: "中止", request: "申请", submit: "提交",
  archive: "归档", preview: "预览", deprecate: "弃用", import: "导入", review: "复核",
  assign: "分派", dismiss: "驳回", reopen: "重新打开", discard: "废弃",
};

export function capabilityLabel(key: string): string {
  const explicit = capabilityLabels[key] ?? grantLabel(key);
  if (explicit !== key) return explicit;
  const split = key.lastIndexOf(".");
  const subject = subjects[key.slice(0, split)];
  const action = actions[key.slice(split + 1)];
  return subject && action ? action + subject : key;
}

export function permissionLabel(key: string): string {
  const split = key.indexOf("/");
  return split < 0 ? capabilityLabel(key) : capabilityLabel(key.slice(0, split)) + " / " + key.slice(split + 1);
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
  principalId: string | null,
  managesOwnProject = false,
): readonly AccessDecision[] {
  if (row.kind === "capability" && row.allowed_decisions) return canManage ? row.allowed_decisions : [];
  if (row.status === "PENDING") {
    if (!canManage) return [];
    return row.requesterId === principalId && !managesOwnProject ? ["reject"] : ["approve", "reject"];
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
