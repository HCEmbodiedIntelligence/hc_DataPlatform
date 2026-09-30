import { z } from "zod";
import { isDatasetId } from "../../entities/dataset";
import { isDatasetVersionId } from "../../entities/dataset-version";
import { isEpisodeId } from "../../entities/episode";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { sameAnnotationDataRef } from "./annotation-package";
import type {
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";

export type TagEpisodeRequest = components["schemas"]["RequestTagEpisode"];
const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const step = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const wire = z
  .object({
    job_id: z.string().regex(/^td_[0-9a-f]{64}$/u),
    status: z.enum(["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED"]),
    phase: z.enum([
      "queued",
      "verifying",
      "deriving",
      "committing",
      "projecting",
      "annotating",
      "completed",
      "failed",
      "cancelled",
    ]),
    generation: z.number().int().positive(),
    attempts: step,
    selection: z
      .object({
        parent: episodeDataReferenceWireSchema,
        parent_collection_version_id: z.string().min(1),
        task_id: z.string().min(1),
        revision: step,
        revision_content_hash: digest,
        annotation_file_sha256: digest,
        review_status: z.enum(["APPROVED", "UNREVIEWED"]),
        annotation_id: z.string().min(1),
        start_step: step,
        end_step: step,
        start_timestamp_ns: z.string().regex(/^[0-9]+$/u),
        next_parent_timestamp_ns: z
          .string()
          .regex(/^[0-9]+$/u)
          .nullable(),
        display_name: z.string().min(1),
        new_episode_id: z.string().min(1),
        new_data_version_id: z.string().regex(/^dv_[0-9a-f]{64}$/u),
      })
      .strict(),
    committed_data: episodeDataReferenceWireSchema.nullable(),
    collection_version_id: z.string().nullable(),
    viewer_target: z
      .object({
        schema_version: z.literal("dataset-ingest-viewer-target/v1"),
        dataset_id:
          z.custom<import("../../entities/dataset").DatasetId>(isDatasetId),
        version_id:
          z.custom<import("../../entities/dataset-version").DatasetVersionId>(
            isDatasetVersionId,
          ),
        episode_id:
          z.custom<import("../../entities/episode").EpisodeId>(isEpisodeId),
        revision_id: z.string().min(1),
      })
      .strict()
      .nullable(),
    annotation_task_id: z.string().nullable(),
    error_code: z.string().nullable(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
  })
  .strict();
export type TagEpisodeJob = z.infer<typeof wire>;

function checked(
  raw: unknown,
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
) {
  const job = wire.parse(raw);
  const s = job.selection,
    parent = s.parent.identity,
    child = job.committed_data,
    viewer = job.viewer_target;
  const revision = history.revisions.find((r) => r.revision === s.revision);
  const tag = revision?.tags.find((t) => t.annotation_id === s.annotation_id);
  if (
    s.task_id !== history.task.task_id ||
    !sameAnnotationDataRef(s.parent, history.task.data_ref) ||
    parent.organization_id !== scope.organizationId ||
    parent.project_id !== scope.projectId ||
    parent.region_code !== scope.regionCode ||
    !revision ||
    revision.content_hash !== s.revision_content_hash ||
    !tag ||
    tag.start_step !== s.start_step ||
    tag.end_step !== s.end_step ||
    s.end_step <= s.start_step ||
    s.end_step > s.parent.frame_count ||
    s.new_episode_id === parent.episode_id ||
    s.new_data_version_id === s.parent.data_version_id ||
    (s.next_parent_timestamp_ns !== null &&
      BigInt(s.next_parent_timestamp_ns) <= BigInt(s.start_timestamp_ns)) ||
    (child !== null &&
      (child.identity.organization_id !== parent.organization_id ||
        child.identity.project_id !== parent.project_id ||
        child.identity.region_code !== parent.region_code ||
        child.identity.dataset_id !== parent.dataset_id ||
        child.identity.episode_id !== s.new_episode_id ||
        child.identity.rollout_id === parent.rollout_id ||
        child.data_version_id !== s.new_data_version_id ||
        child.frame_count !== s.end_step - s.start_step)) ||
    (job.collection_version_id === null) !== (viewer === null) ||
    (viewer !== null &&
      (!child ||
        viewer.dataset_id !== child.identity.dataset_id ||
        viewer.episode_id !== child.identity.episode_id ||
        viewer.version_id !== "version_" + job.collection_version_id)) ||
    (job.annotation_task_id !== null &&
      (!viewer || job.annotation_task_id === history.task.task_id)) ||
    (job.status === "READY" && (!viewer || !job.annotation_task_id))
  )
    throw new Error(
      "新 Episode 任务的父版本、Tag 区间或发布结果不一致，请刷新页面。",
    );
  return job;
}
function base(scope: RuntimeAnnotationScope, taskId: string) {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/annotation-tasks/${encodeURIComponent(taskId)}/tag-episodes`;
}
export async function listTagEpisodes(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  signal?: AbortSignal,
  before?: string,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: base(scope, history.task.task_id),
    scope,
    signal,
    cache: "no-store",
    query: { limit: 25, ...(before ? { before } : {}) },
  });
  return z
    .array(z.unknown())
    .parse(raw)
    .map((item) => checked(item, scope, history));
}
export async function createTagEpisode(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  body: TagEpisodeRequest,
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  const job = checked(
    await request({
      method: "POST",
      path: base(scope, history.task.task_id),
      scope,
      body,
      idempotencyKey,
      signal,
      cache: "no-store",
    }),
    scope,
    history,
  );
  if (
    job.selection.revision !== body.revision ||
    job.selection.annotation_id !== body.annotation_id ||
    (body.display_name != null &&
      job.selection.display_name !== body.display_name) ||
    (!body.allow_unreviewed && job.selection.review_status !== "APPROVED")
  ) {
    throw new Error("返回的新 Episode 任务与本次选择不一致。");
  }
  return job;
}
export async function commandTagEpisode(
  scope: RuntimeAnnotationScope,
  history: RuntimeAnnotationHistory,
  job: TagEpisodeJob,
  command: "retry" | "cancel",
  signal?: AbortSignal,
) {
  const expected = checked(job, scope, history);
  const result = checked(
    await request({
      method: "POST",
      path: `${base(scope, history.task.task_id)}/${job.job_id}/${command}`,
      scope,
      body: { expected_generation: job.generation },
      signal,
      cache: "no-store",
    }),
    scope,
    history,
  );
  if (
    result.job_id !== job.job_id ||
    JSON.stringify(result.selection) !== JSON.stringify(expected.selection)
  )
    throw new Error("返回的任务与选定任务不一致。");
  return result;
}
