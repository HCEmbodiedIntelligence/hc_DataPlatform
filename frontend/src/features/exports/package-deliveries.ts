import { z } from "zod";
import type { Scope } from "../../shared/scope/model";
import { episodeDataReferenceWireSchema as dataWire } from "../datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import { completeDeliveryJobWire } from "../datasets/episodes/complete-deliveries";
import { annotationDeliveryPlanWire } from "../annotation/runtime/annotation-deliveries";

export type PackageData = z.infer<typeof dataWire>;
export type BatchMode = "independent" | "rgbd" | "rgb";
export type AnnotationSelection = {
  task_id: string;
  revision: number;
  allow_unreviewed: boolean;
  track: {
    format: "hc-subtask-track/1";
    track_id: string;
    label: string;
    annotation_ids: string[];
  };
};
export type PackageSelection = {
  data: PackageData;
  annotation?: AnnotationSelection | null;
};
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const collectionPlan = z
  .object({
    format: z.literal("hc-collection-delivery-plan/1"),
    members: z
      .array(
        z
          .object({
            data: dataWire,
            manifest_sha256: digest,
            annotation: annotationDeliveryPlanWire.nullable(),
            selected_depth_profiles: z.record(z.string(), digest),
            excluded_depth_features: z.array(z.string()),
          })
          .strict(),
      )
      .min(1)
      .max(100),
    modalities: z.enum(["rgb", "rgbd"]),
    archive_format: z.literal("lerobot_collection_zip/1"),
    annotation_selection: z.literal("per_episode_fixed_selection"),
    contract_sha256: digest,
    processor_version: z.literal("hc-lerobot-collection/1"),
    archive_version: z.literal("hc-collection-archive/1"),
    reader_version: z.literal("lerobot/0.6.1"),
  })
  .strict();
const jobWire = z.union([
  completeDeliveryJobWire,
  completeDeliveryJobWire.extend({
    plan: collectionPlan,
    total_files: count.positive().nullable(),
    total_bytes: count.positive().nullable(),
    phase: z.enum([
      "queued",
      "verifying",
      "projecting",
      "archiving",
      "uploading",
      "completed",
      "failed",
      "cancelled",
    ]),
  }),
]);
export type PackageJob = z.infer<typeof jobWire>;

function base(scope: Scope) {
  if (!scope.organizationId || !scope.projectId || !scope.regionCode)
    throw new Error("请选择组织、项目和区域。");
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/episode-deliveries`;
}
export function sameData(left: PackageData, right: PackageData) {
  return (
    JSON.stringify(dataWire.parse(left)) ===
    JSON.stringify(dataWire.parse(right))
  );
}
function scopeData(data: PackageData, scope: Scope) {
  if (
    data.identity.organization_id !== scope.organizationId ||
    data.identity.project_id !== scope.projectId ||
    data.identity.region_code !== scope.regionCode
  )
    throw new Error("数据引用超出当前范围。");
}
export function checkedPackageJob(raw: unknown, scope: Scope) {
  const job = jobWire.parse(raw),
    { plan } = job;
  plan.members.forEach((m) => scopeData(m.data, scope));
  if (
    new Set(plan.members.map((m) => m.data.data_version_id)).size !==
      plan.members.length ||
    new Set(plan.members.map((m) => JSON.stringify(m.data.identity))).size !==
      plan.members.length ||
    job.processed_files > (job.total_files ?? 0) ||
    job.processed_bytes > (job.total_bytes ?? 0) ||
    (job.total_files === null) !== (job.total_bytes === null) ||
    job.uploaded_bytes > (job.size_bytes ?? 0) ||
    (job.status === "READY" &&
      (!job.artifact_sha256 ||
        !job.size_bytes ||
        !job.total_files ||
        !job.total_bytes ||
        job.phase !== "completed" ||
        job.processed_files !== job.total_files ||
        job.processed_bytes !== job.total_bytes ||
        job.uploaded_bytes !== job.size_bytes)) ||
    (job.status === "FAILED") !== (job.error_code !== null)
  )
    throw new Error("任务的固定选择或归档状态不一致。");
  if (plan.format === "hc-collection-delivery-plan/1") {
    for (const member of plan.members) {
      const a = member.annotation;
      if (
        (plan.modalities === "rgb" &&
          Object.keys(member.selected_depth_profiles).length > 0) ||
        (plan.modalities === "rgbd" &&
          member.excluded_depth_features.length > 0) ||
        (a &&
          (!sameData(a.binding.data, member.data) ||
            a.parent_manifest_sha256 !== member.manifest_sha256 ||
            (a.review.status === "APPROVED" &&
              (!a.review.review_id || !a.review.submission_id))))
      )
        throw new Error("集合的深度选择或标注绑定不一致。");
    }
  } else if (
    (plan.archive_format === "independent_episode_zip/1" &&
      plan.members.length !== 1) ||
    plan.modalities !== "all_collected" ||
    plan.members.some(
      (m, i) =>
        m.root !==
        (plan.archive_format === "independent_episode_zip/1"
          ? "episode"
          : `episodes/${i.toString().padStart(6, "0")}`),
    )
  )
    throw new Error("独立包归档布局不一致。");
  return job;
}

export async function loadPackageCandidates(
  scope: Scope,
  datasetId: string,
  signal?: AbortSignal,
  page?: { collectionId: string; offset: number },
) {
  const wire = z
    .object({
      dataset_id: z.string(),
      collection_version_id: z.string().nullable(),
      version: count.positive().nullable(),
      items: z.array(dataWire),
      total: count,
      legacy_episode_count: count,
      next_offset: count.nullable(),
    })
    .strict();
  const value = wire.parse(
    await request({
      method: "GET",
      path: base(scope) + "/candidates",
      scope,
      signal,
      cache: "no-store",
      query: {
        dataset_id: datasetId,
        limit: 25,
        ...(page
          ? { collection_version_id: page.collectionId, offset: page.offset }
          : {}),
      },
    }),
  );
  if (
    value.dataset_id !== datasetId ||
    (page && value.collection_version_id !== page.collectionId) ||
    value.items.some((d) => d.identity.dataset_id !== datasetId) ||
    (value.next_offset !== null && value.next_offset <= (page?.offset ?? 0))
  )
    throw new Error("候选数据与固定集合快照不一致。");
  value.items.forEach((d) => scopeData(d, scope));
  return value;
}
export async function listPackageJobs(
  scope: Scope,
  signal?: AbortSignal,
  before?: string,
) {
  const raw = await request({
    method: "GET",
    path: base(scope),
    scope,
    signal,
    cache: "no-store",
    query: { limit: 25, ...(before ? { before } : {}) },
  });
  return z
    .array(z.unknown())
    .parse(raw)
    .map((j) => checkedPackageJob(j, scope));
}
export async function createPackageBatch(
  scope: Scope,
  members: PackageSelection[],
  mode: BatchMode,
  key: string,
  signal?: AbortSignal,
) {
  if (!members.length || members.length > 100)
    throw new Error("请选择 1–100 条 Episode。");
  members.forEach((m) => scopeData(dataWire.parse(m.data), scope));
  if (mode === "independent" && members.some((m) => m.annotation))
    throw new Error("带选定标注的批量交付请选择 LeRobot 集合。");
  const body =
    mode === "independent"
      ? {
          data: members.map((m) => m.data),
          archive_format: "independent_episode_batch_zip/1",
        }
      : { members, modalities: mode };
  const job = checkedPackageJob(
    await request({
      method: "POST",
      path: base(scope) + (mode === "independent" ? "" : "/collections"),
      scope,
      signal,
      cache: "no-store",
      idempotencyKey: key,
      body,
    }),
    scope,
  );
  if (
    job.plan.members.length !== members.length ||
    job.plan.members.some((m) => !members.some((s) => sameData(s.data, m.data)))
  )
    throw new Error("交付任务与所选数据版本不一致。");
  if (mode === "independent") {
    if (job.plan.archive_format !== "independent_episode_batch_zip/1")
      throw new Error("交付类型与批量独立包不一致。");
  } else {
    if (
      job.plan.format !== "hc-collection-delivery-plan/1" ||
      job.plan.modalities !== mode
    )
      throw new Error("集合模态与所选格式不一致。");
    for (const m of job.plan.members) {
      const wanted = members.find((s) => sameData(s.data, m.data))!.annotation;
      const actual = m.annotation;
      if (
        Boolean(wanted) !== Boolean(actual) ||
        (wanted &&
          actual &&
          (wanted.task_id !== actual.binding.task_id ||
            wanted.revision !== actual.binding.revision ||
            wanted.track.track_id !== actual.binding.track.track_id ||
            wanted.track.label !== actual.binding.track.label ||
            JSON.stringify([...wanted.track.annotation_ids].sort()) !==
              JSON.stringify([...actual.binding.track.annotation_ids].sort()) ||
            (!wanted.allow_unreviewed && actual.review.status !== "APPROVED")))
      )
        throw new Error("集合标注与所选固定修订/轨道不一致。");
    }
  }
  return job;
}
export async function commandPackageJob(
  scope: Scope,
  job: PackageJob,
  command: "cancel" | "retry",
  signal?: AbortSignal,
) {
  checkedPackageJob(job, scope);
  const result = checkedPackageJob(
    await request({
      method: "POST",
      path: `${base(scope)}/${job.job_id}/${command}`,
      scope,
      signal,
      cache: "no-store",
      body: { expected_generation: job.generation },
    }),
    scope,
  );
  if (
    result.job_id !== job.job_id ||
    JSON.stringify(result.plan) !== JSON.stringify(job.plan) ||
    result.generation !== job.generation + (command === "retry" ? 1 : 0) ||
    result.status !== (command === "retry" ? "PENDING" : "CANCELLED")
  )
    throw new Error("任务操作的代次或状态不一致。");
  return result;
}
export async function downloadPackageJob(
  scope: Scope,
  job: PackageJob,
  signal?: AbortSignal,
) {
  checkedPackageJob(job, scope);
  if (job.status !== "READY") throw new Error("交付尚未就绪。");
  const grant = z
    .object({
      job_id: z.string(),
      artifact_sha256: digest,
      size_bytes: count.positive(),
      url: z.url(),
      expires_seconds: z.literal(300),
    })
    .strict()
    .parse(
      await request({
        method: "POST",
        path: `${base(scope)}/${job.job_id}/download`,
        scope,
        signal,
        cache: "no-store",
      }),
    );
  const url = new URL(grant.url);
  if (
    grant.job_id !== job.job_id ||
    grant.artifact_sha256 !== job.artifact_sha256 ||
    grant.size_bytes !== job.size_bytes ||
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("下载授权与归档身份不一致。");
  return grant;
}

const tagWire = z
  .object({
    annotation_id: z.string(),
    tag_id: z.string(),
    label: z.string().nullable().optional(),
    start_step: count,
    end_step: count.positive(),
  })
  .passthrough();
export async function loadCollectionAnnotationHistory(
  scope: Scope,
  data: PackageData,
  taskId: string,
  signal?: AbortSignal,
) {
  scopeData(data, scope);
  const history = z
    .object({
      task: z
        .object({
          task_id: z.string(),
          data_ref: dataWire,
          current_revision: count,
          approved_revision: count.nullable(),
        })
        .passthrough(),
      revisions: z.array(
        z
          .object({
            task_id: z.string(),
            data_ref: dataWire,
            revision: count,
            content_hash: digest,
            tags: z.array(tagWire),
          })
          .passthrough(),
      ),
    })
    .passthrough()
    .parse(
      await request({
        method: "GET",
        path: `/annotation-tasks/${encodeURIComponent(taskId)}/history`,
        scope,
        signal,
        cache: "no-store",
      }),
    );
  if (
    history.task.task_id !== taskId ||
    !sameData(history.task.data_ref, data) ||
    history.revisions.some(
      (r) =>
        r.task_id !== taskId ||
        !sameData(r.data_ref, data) ||
        r.tags.some(
          (t) => t.start_step >= t.end_step || t.end_step > data.frame_count,
        ),
    )
  )
    throw new Error("标注历史与固定数据版本不一致。");
  return history;
}
