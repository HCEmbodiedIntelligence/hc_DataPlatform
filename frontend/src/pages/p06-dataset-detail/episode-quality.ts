import { z } from "zod";
import type { Scope } from "../../shared/scope/model";
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

export {
  qualityRuleLabels,
  qualityReasonLabels,
} from "../../features/quality/labels";
