import { z } from "zod";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { sameAnnotationDataRef } from "./annotation-package";
import type {
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";

export type DeliveryRequest =
  components["schemas"]["RequestAnnotationDelivery"];
const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const wire = z
  .object({
    job_id: z.string().regex(/^ad_[0-9a-f]{64}$/u),
    plan: z
      .object({
        format: z.literal("hc-annotation-delivery-plan/1"),
        binding: z
          .object({
            data: episodeDataReferenceWireSchema,
            task_id: z.string().min(1),
            revision: z.number().int().nonnegative(),
            revision_content_hash: digest,
            annotation_file_sha256: digest,
            track: z
              .object({
                format: z.literal("hc-subtask-track/1"),
                track_id: z.string().min(1),
                label: z.string().min(1),
                annotation_ids: z.array(z.string().min(1)).min(1),
              })
              .strict(),
          })
          .strict(),
        annotation_size: z.number().int().positive(),
        review: z
          .object({
            status: z.enum(["APPROVED", "UNREVIEWED"]),
            review_id: z.string().nullable(),
            submission_id: z.string().nullable(),
          })
          .strict(),
        approval: z.record(z.string(), z.unknown()).nullable(),
        submission: z.record(z.string(), z.unknown()).nullable(),
        parent_manifest_sha256: digest,
        processor_version: z.literal("hc-annotated-delivery-queue/1"),
        reader_version: z.literal("lerobot/0.6.1"),
        archive_format: z.literal("independent_episode_zip/1"),
        includes_collected_depth: z.literal(true),
      })
      .strict(),
    status: z.enum(["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED"]),
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
    generation: z.number().int().positive(),
    attempts: z.number().int().nonnegative(),
    artifact_sha256: digest.nullable(),
    size_bytes: z.number().int().positive().nullable(),
    manifest_sha256: digest.nullable(),
    error_code: z.string().nullable(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
  })
  .strict();
export type DeliveryJob = z.infer<typeof wire>;

function checked(
  raw: unknown,
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
): DeliveryJob {
  const job = wire.parse(raw);
  const { binding, review } = job.plan;
  const revision = history.revisions.find(
    (item) => item.revision === binding.revision,
  );
  const identity = binding.data.identity;
  if (
    binding.task_id !== history.task.task_id ||
    !revision ||
    revision.content_hash !== binding.revision_content_hash ||
    !sameAnnotationDataRef(binding.data, history.task.data_ref) ||
    identity.organization_id !== scope.organizationId ||
    identity.project_id !== scope.projectId ||
    identity.region_code !== scope.regionCode ||
    (review.status === "APPROVED" &&
      (!review.review_id || !review.submission_id)) ||
    (job.status === "READY" &&
      (!job.artifact_sha256 || !job.size_bytes || !job.manifest_sha256))
  ) {
    throw new Error("下载任务的数据版本、修订或审核身份不一致，请刷新页面。");
  }
  return job;
}
function base(taskId: string) {
  return `/annotation-tasks/${encodeURIComponent(taskId)}/deliveries`;
}

export async function listAnnotationDeliveries(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  signal?: AbortSignal,
  before?: string,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: base(history.task.task_id),
    scope,
    signal,
    query: { limit: 25, ...(before ? { before } : {}) },
    cache: "no-store",
  });
  return z
    .array(z.unknown())
    .parse(raw)
    .map((value) => checked(value, scope, history));
}

export async function createAnnotationDelivery(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  body: DeliveryRequest,
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  return checked(
    await request({
      method: "POST",
      path: base(history.task.task_id),
      scope,
      body,
      idempotencyKey,
      signal,
      cache: "no-store",
    }),
    scope,
    history,
  );
}

export async function commandAnnotationDelivery(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  job: DeliveryJob,
  command: "retry" | "cancel",
  signal?: AbortSignal,
) {
  return checked(
    await request({
      method: "POST",
      path: `${base(history.task.task_id)}/${job.job_id}/${command}`,
      scope,
      body: { expected_generation: job.generation },
      signal,
      cache: "no-store",
    }),
    scope,
    history,
  );
}

export async function authorizeAnnotationDelivery(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  job: DeliveryJob,
  signal?: AbortSignal,
) {
  checked(job, scope, history);
  if (job.status !== "READY") throw new Error("打包尚未完成。");
  const grant = z
    .object({
      job_id: z.string(),
      artifact_sha256: digest,
      size_bytes: z.number().int().positive(),
      url: z.url(),
      expires_seconds: z.literal(300),
    })
    .strict()
    .parse(
      await request({
        method: "POST",
        path: `${base(history.task.task_id)}/${job.job_id}/download`,
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
  ) {
    throw new Error("下载授权与选定归档不一致。");
  }
  return grant;
}
