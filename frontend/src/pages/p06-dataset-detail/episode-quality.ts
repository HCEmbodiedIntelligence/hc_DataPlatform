import { z } from "zod";
import type { Scope } from "../../entities/scope";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";

const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const scalar = z.union([
  z.boolean(),
  z.number().finite(),
  z.string(),
  z.null(),
]);
const finding = z
  .object({
    code: z.string(),
    severity: z.enum(["warning", "error"]),
    message: z.string(),
    topic: z.string(),
    start_ns: z.number(),
    end_ns: z.number(),
    observed: scalar,
    threshold: scalar,
  })
  .strict();
const rule = z
  .object({
    code: z.string().startsWith("QC_"),
    topic: z.string().nullable(),
    status: z.enum(["pass", "warn", "fail", "skip", "info"]),
    reason: z.string().nullable(),
    participates_in_verdict: z.boolean().nullable(),
    evaluated_count: z.number().int().nonnegative(),
    expected_count: z.number().int().nonnegative(),
    observed: z.record(z.string(), scalar),
    thresholds: z.record(z.string(), scalar),
    detector_findings: z.array(finding),
    report_finding_indexes: z.array(z.number().int().nonnegative()),
  })
  .strict();
const report = z.object({
  schema_version: z.literal("qc-report/v1"),
  rollout_id: z.string(),
  source_sha256: digest,
  content_sha256: digest,
  profile_sha256: digest,
  profile_id: z.string().min(1),
  engine_version: z.string(),
  status: z.enum(["PASS", "RISK", "REJECT"]),
  findings: z.array(finding),
});
const evidence = z.object({
  format: z.literal("hc-qc-rule-evidence/1"),
  rollout_id: z.string(),
  source_sha256: digest,
  report_sha256: digest,
  content_sha256: digest,
  rules: z.array(rule),
});
const wire = z
  .object({
    data: episodeDataReferenceWireSchema,
    status: z.enum(["PASS", "RISK", "REJECT", "UNKNOWN"]),
    evidence_status: z.enum(["AVAILABLE", "LEGACY_MISSING", "REPORT_MISSING"]),
    report: report.nullable(),
    evidence: evidence.nullable(),
    rules: z.array(rule),
    additional_finding_codes: z.array(z.string()),
  })
  .strict();
export type EpisodeQuality = z.infer<typeof wire>;

export async function loadEpisodeQuality(
  scope: Scope,
  data: ReviewDataRef,
  signal?: AbortSignal,
) {
  const id = data.identity;
  if (
    id.organization_id !== scope.organizationId ||
    id.project_id !== scope.projectId ||
    id.region_code !== scope.regionCode
  ) {
    throw new Error("质检数据超出当前组织、项目或区域。");
  }
  const path = `/projects/${encodeURIComponent(id.project_id)}/regions/${encodeURIComponent(id.region_code)}/datasets/${encodeURIComponent(id.dataset_id)}/episodes/${encodeURIComponent(id.episode_id)}/data-versions/${encodeURIComponent(data.data_version_id)}/quality`;
  const result = wire.parse(
    await request<unknown>({
      method: "GET",
      path,
      scope,
      signal,
      cache: "no-store",
    }),
  );
  if (
    JSON.stringify(result.data) !==
      JSON.stringify(episodeDataReferenceWireSchema.parse(data)) ||
    (result.evidence_status === "AVAILABLE") !== (result.evidence !== null) ||
    (result.evidence_status === "REPORT_MISSING") !==
      (result.report === null) ||
    (result.report !== null && result.report.rollout_id !== id.rollout_id) ||
    (result.evidence !== null &&
      (result.evidence.report_sha256 !== result.report?.content_sha256 ||
        result.evidence.rollout_id !== id.rollout_id ||
        result.evidence.source_sha256 !== result.report?.source_sha256 ||
        JSON.stringify(result.evidence.rules) !==
          JSON.stringify(result.rules))) ||
    result.rules.some(
      (item) =>
        (item.status === "pass" &&
          (result.evidence === null ||
            item.reason !== null ||
            item.participates_in_verdict !== true ||
            item.evaluated_count === 0 ||
            item.evaluated_count !== item.expected_count ||
            item.detector_findings.length > 0)) ||
        ((item.status === "skip" || item.status === "info") && !item.reason) ||
        item.report_finding_indexes.some(
          (index) =>
            result.report?.findings[index]?.code !== item.code ||
            result.report?.findings[index]?.topic !== item.topic,
        ),
    )
  ) {
    throw new Error("质检证据与固定数据版本或执行结果不一致。");
  }
  return result;
}

export const qualityRuleLabels: Record<string, string> = {
  QC_REQUIRED_TOPIC_MISSING: "必需通道",
  QC_TIMESTAMP_DUPLICATE: "重复时间戳",
  QC_TIMESTAMP_BACKWARD: "时间戳倒退",
  QC_FREQUENCY_LOW: "采样频率",
  QC_GAP_EXCESSIVE: "采样间隔",
  QC_CONSECUTIVE_FRAMES_MISSING: "连续缺帧",
  QC_COVERAGE_LOW: "时间覆盖",
  QC_IMAGE_BLACK: "RGB 黑帧",
  QC_IMAGE_REPEATED: "RGB 重复帧",
  QC_IMAGE_CORRUPT: "图像解码",
  QC_JOINT_OUT_OF_RANGE: "关节范围",
  QC_ACTION_MISSING: "动作数据数量",
  QC_ACTION_JUMP: "相邻动作变化",
  QC_POINT_CLOUD_EMPTY: "空点云",
  QC_POINT_COUNT_ABNORMAL: "点云点数",
  QC_MODALITY_OFFSET: "模态时间偏差",
  QC_COMPLETE_STEP_RATIO_LOW: "完整同步步比例",
};
export const qualityReasonLabels: Record<string, string> = {
  UNSUPPORTED_ENGINE_EVIDENCE: "此引擎版本的执行证据尚未适配",
  NO_OBSERVATIONS: "缺少可评估观测",
  PARTIAL_OBSERVATIONS: "只有部分观测可评估",
  INSUFFICIENT_OBSERVATIONS: "观测数量不足",
  EXCLUDED_BY_VERDICT_POLICY: "此规则不参与当前策略的总判定",
  NO_REQUIRED_TOPICS: "未配置必需通道",
  NO_JOINT_LIMITS: "未配置关节范围",
  ACTION_COUNT_NOT_REQUIRED: "未设置动作数量要求",
  NO_POINT_COUNT_LIMITS: "未配置点数范围",
  COMPLETE_STEP_RATIO_NOT_MEASURED: "尚未测量完整同步步比例",
  LEGACY_EXECUTION_EVIDENCE_MISSING: "历史报告缺少逐规则执行证据",
  QC_REPORT_MISSING: "尚无可验证的 QC 报告",
};
