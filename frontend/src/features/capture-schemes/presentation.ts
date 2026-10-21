import { isDomainError } from "../../shared/api/domain-error";
import type { Difference, SchemeDocument } from "./contracts";
export const captureProblemLabels: Record<string, string> = {
  CAPTURE_MAPPING_NOT_FOUND: "设备映射版本不存在",
  CAPTURE_MAPPING_SAMPLE_MISMATCH: "请选择来自当前实例的验证样本",
  CAPTURE_MAPPING_INVALID: "设备来源映射不符合方案",
  CAPTURE_DATASET_REVIEW_REQUIRED: "已有数据集结构需要确认",
  CAPTURE_PARTICIPANT_REQUIRED: "当前实例未加入任务参与列表",
  CAPTURE_PLAN_TARGET_MISMATCH: "采集计划与任务归属不符",
  CAPTURE_RECORDING_CONFLICT: "录制已绑定另一份采集计划",
  CAPTURE_REGION_MISMATCH: "采集计划所属区域不一致",
  CAPTURE_ROBOT_UNAVAILABLE: "机器人实例未启用或不可用",
  CAPTURE_PLAN_REQUIRED: "任务采集方案待配置",
  CAPTURE_SCHEME_REQUIRED: "采集方案待配置",
  CAPTURE_ASSOCIATION_REQUIRED: "实例采集方案待配置",
  CAPTURE_MAPPING_UNVERIFIED: "设备映射待验证",
  CAPTURE_MAPPING_MISMATCH: "映射与方案版本不符",
  CAPTURE_VERSION_MISMATCH: "采集方案版本不符",
  CAPTURE_STRUCTURE_MISMATCH: "实际数据结构与方案不符",
  CAPTURE_SOURCE_INVALID: "原始文件内容无效或损坏",
  CAPTURE_DATASET_INCOMPATIBLE: "任务结构与已有数据集不兼容",
  CAPTURE_MODEL_INCOMPATIBLE: "机器人型号不兼容",
  CAPTURE_FORMAT_UNSUPPORTED: "方案不支持该输入格式",
  CAPTURE_SAMPLE_VALIDATION_REQUIRED: "当前方案需要样本验证",
  CAPTURE_REVISION_CONFLICT: "版本已变化，请重新加载后核对修改",
  CAPTURE_PLAN_LOCKED: "任务计划已固定，请为新结构创建新任务",
  CAPTURE_SCHEME_ARCHIVED: "方案已归档",
  ANNOTATION_PENDING: "标注待配置",
  ANNOTATION_NOT_CONFIGURED: "标注待配置",
};
export function captureError(error: unknown): string {
  if (isDomainError(error))
    return [
      captureProblemLabels[error.problemCode ?? ""] ??
        captureProblemLabels[error.code],
      error.message,
      ...error.fieldErrors.map((e) => `${e.path}：${e.message}`),
    ]
      .filter(Boolean)
      .join("；");
  return error instanceof Error ? error.message : "操作未完成，请重试";
}
export function dataSummary(doc: SchemeDocument) {
  const parts = Object.values(doc.output.features);
  const frequency =
    doc.output.frequency_hz == null
      ? "帧率从样本自动读取"
      : `${doc.output.frequency_hz} Hz（保留）`;
  return `${parts.filter((f) => f.role === "state").reduce((n, f) => n + (f.shape[0] ?? 0), 0)} 维状态 · ${parts.filter((f) => f.role === "action").reduce((n, f) => n + (f.shape[0] ?? 0), 0)} 维动作 · ${parts.filter((f) => ["rgb", "depth"].includes(f.role)).length} 路相机 · ${frequency}`;
}
export function structureDiff(
  before: unknown,
  after: unknown,
  path = "",
): Difference[] {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (
    before &&
    after &&
    typeof before === "object" &&
    typeof after === "object" &&
    !Array.isArray(before) &&
    !Array.isArray(after)
  ) {
    const left = before as Record<string, unknown>,
      right = after as Record<string, unknown>;
    return [...new Set([...Object.keys(left), ...Object.keys(right)])]
      .sort()
      .flatMap((key) =>
        structureDiff(left[key], right[key], path ? `${path}.${key}` : key),
      );
  }
  return [{ path, expected: before, actual: after }];
}
export const presenceLabel = (
  value: unknown,
  present = value !== null && value !== undefined,
) => (present ? String(value) : "未采集");

export function annotationStatusLabel(status?: string) {
  if (status === "APPROVED") return "标注已审核通过";
  return status === "PENDING_CONFIGURATION"
    ? "标注待配置"
    : status === "CREATED"
      ? "已创建标注任务"
      : status === "FAILED"
        ? "标注创建失败（数据入库结果保留）"
        : "标注状态未提供";
}
