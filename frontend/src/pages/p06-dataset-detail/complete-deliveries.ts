import { z } from "zod";
import type { Scope } from "../../entities/scope";
import { episodeDataReferenceWireSchema as dataWire } from "../../features/datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import type { ReviewDataRef } from "./data-reviews";
import { episodeEndpoint } from "./episode-detail";

const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const jobWire = z
  .object({
    job_id: z.string().regex(/^ed_[0-9a-f]{64}$/u),
    plan: z
      .object({
        format: z.literal("hc-episode-delivery-plan/1"),
        members: z
          .array(
            z
              .object({
                data: dataWire,
                manifest_sha256: digest,
                root: z.string().regex(/^(episode|episodes\/[0-9]{6})$/u),
                file_count: count.positive(),
                byte_count: count.positive(),
              })
              .strict(),
          )
          .min(1)
          .max(100),
        archive_format: z.enum([
          "independent_episode_zip/1",
          "independent_episode_batch_zip/1",
        ]),
        modalities: z.literal("all_collected"),
        annotation_selection: z.literal("package_contents_only"),
        processor_version: z.literal("hc-episode-archive/1"),
        reader_version: z.literal("lerobot/0.6.1"),
      })
      .strict(),
    status: z.enum(["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED"]),
    phase: z.enum([
      "queued",
      "verifying",
      "archiving",
      "uploading",
      "completed",
      "failed",
      "cancelled",
    ]),
    generation: count.positive(),
    attempts: count,
    processed_files: count,
    total_files: count.positive(),
    processed_bytes: count,
    total_bytes: count.positive(),
    uploaded_bytes: count,
    artifact_sha256: digest.nullable(),
    size_bytes: count.positive().nullable(),
    error_code: z.string().nullable(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
  })
  .strict();
export type CompleteDeliveryJob = z.infer<typeof jobWire>;
export const completeDeliveryJobWire = jobWire;

function base(scope: Scope, data: ReviewDataRef) {
  episodeEndpoint(scope, data); // Same immutable scope guard as all Episode detail requests.
  return `/projects/${encodeURIComponent(data.identity.project_id)}/regions/${encodeURIComponent(data.identity.region_code)}/episode-deliveries`;
}
function same(left: ReviewDataRef, right: ReviewDataRef) {
  return (
    JSON.stringify(dataWire.parse(left)) ===
    JSON.stringify(dataWire.parse(right))
  );
}
function checked(raw: unknown, scope: Scope, data: ReviewDataRef) {
  const job = jobWire.parse(raw);
  const { members } = job.plan;
  if (
    !members.some((m) => same(m.data, data)) ||
    members.some((m, index) => {
      const identity = m.data.identity;
      return (
        identity.organization_id !== scope.organizationId ||
        identity.project_id !== scope.projectId ||
        identity.region_code !== scope.regionCode ||
        m.root !==
          (job.plan.archive_format === "independent_episode_zip/1"
            ? "episode"
            : `episodes/${index.toString().padStart(6, "0")}`)
      );
    }) ||
    new Set(members.map((m) => m.data.data_version_id)).size !==
      members.length ||
    (job.plan.archive_format === "independent_episode_zip/1" &&
      members.length !== 1) ||
    job.processed_bytes > job.total_bytes ||
    job.processed_files > job.total_files ||
    job.uploaded_bytes > (job.size_bytes ?? 0) ||
    (job.status === "READY" &&
      (!job.artifact_sha256 ||
        !job.size_bytes ||
        job.phase !== "completed" ||
        job.processed_bytes !== job.total_bytes ||
        job.processed_files !== job.total_files ||
        job.uploaded_bytes !== job.size_bytes)) ||
    (job.status === "FAILED") !== (job.error_code !== null)
  )
    throw new Error("完整包任务的数据版本、范围或文件状态不一致。");
  return job;
}
export async function listCompleteDeliveries(
  scope: Scope,
  data: ReviewDataRef,
  signal?: AbortSignal,
) {
  const raw = await request({
    method: "GET",
    path: base(scope, data),
    scope,
    signal,
    cache: "no-store",
    query: {
      data_version_id: data.data_version_id,
      limit: 25,
      kind: "independent",
    },
  });
  return z
    .array(z.unknown())
    .parse(raw)
    .map((job) => checked(job, scope, data));
}
export async function requestCompleteDelivery(
  scope: Scope,
  data: ReviewDataRef,
  key: string,
  signal?: AbortSignal,
) {
  const job = checked(
    await request({
      method: "POST",
      path: base(scope, data),
      scope,
      signal,
      cache: "no-store",
      idempotencyKey: key,
      body: { data: [data], archive_format: "independent_episode_zip/1" },
    }),
    scope,
    data,
  );
  if (
    job.plan.members.length !== 1 ||
    job.plan.archive_format !== "independent_episode_zip/1"
  )
    throw new Error("下载任务与所选单条 Episode 不一致。");
  return job;
}
export async function commandCompleteDelivery(
  scope: Scope,
  data: ReviewDataRef,
  job: CompleteDeliveryJob,
  command: "cancel" | "retry",
  signal?: AbortSignal,
) {
  checked(job, scope, data);
  const result = checked(
    await request({
      method: "POST",
      path: `${base(scope, data)}/${job.job_id}/${command}`,
      scope,
      signal,
      cache: "no-store",
      body: { expected_generation: job.generation },
    }),
    scope,
    data,
  );
  if (
    result.job_id !== job.job_id ||
    result.generation !== job.generation + (command === "retry" ? 1 : 0) ||
    result.status !== (command === "retry" ? "PENDING" : "CANCELLED")
  )
    throw new Error("打包操作返回的任务或重试状态不一致。");
  return result;
}
export async function authorizeCompleteDelivery(
  scope: Scope,
  data: ReviewDataRef,
  job: CompleteDeliveryJob,
  signal?: AbortSignal,
) {
  checked(job, scope, data);
  if (job.status !== "READY") throw new Error("完整包尚未就绪。");
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
        path: `${base(scope, data)}/${job.job_id}/download`,
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
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("下载授权与完整包身份不一致。");
  return grant;
}
