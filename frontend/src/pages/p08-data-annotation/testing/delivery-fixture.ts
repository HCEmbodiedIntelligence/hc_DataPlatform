import type { DeliveryJob } from "../annotation-deliveries";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope as scope,
} from "./annotation-fixture";

export function deliveryFixture() {
  const bundle = createVisualAnnotationBundle({ mode: "annotation" });
  if (bundle.task.base_step_count == null)
    throw new Error("fixture needs a frame count");
  const data = {
    identity: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
      dataset_id: bundle.task.dataset_id,
      rollout_id: bundle.task.rollout_id,
      episode_id: "episode-fixed",
    },
    data_version_id: "dv_" + "a".repeat(64),
    version: 2,
    storage_format: "lerobot_v3" as const,
    content_hash: "b".repeat(64),
    schema_snapshot_id: "schema",
    package_prefix: "episodes/fixed",
    frame_count: bundle.task.base_step_count,
  };
  const tags = [
    {
      annotation_id: "first",
      tag_id: "a",
      path: ["a"],
      label: "第一段",
      start_step: 0,
      end_step: 2,
      attributes: {},
      relations: [],
    },
    {
      annotation_id: "cross",
      tag_id: "b",
      path: ["b"],
      label: "交叉标签",
      start_step: 1,
      end_step: 4,
      attributes: {},
      relations: [],
    },
  ];
  const binding = { data_ref: data, base_lance_version: null };
  const task = {
    ...bundle.task,
    ...binding,
    creation_source: "SYSTEM_EPISODE" as const,
    approved_revision: null,
  };
  const history = {
    ...bundle.history,
    task,
    revisions: bundle.history.revisions.map((r) => ({
      ...r,
      ...binding,
      tags,
    })),
    submissions: bundle.history.submissions.map((s) => ({ ...s, ...binding })),
    reviews: bundle.history.reviews.map((r) => ({ ...r, data_ref: data })),
  };
  const revision = history.revisions.at(-1)!;
  const job: DeliveryJob = {
    job_id: "ad_" + "c".repeat(64),
    plan: {
      format: "hc-annotation-delivery-plan/1",
      binding: {
        data,
        task_id: task.task_id,
        revision: revision.revision,
        revision_content_hash: revision.content_hash,
        annotation_file_sha256: "d".repeat(64),
        track: {
          format: "hc-subtask-track/1",
          track_id: "task-segments",
          label: "任务分段",
          annotation_ids: ["first"],
        },
      },
      annotation_size: 100,
      review: { status: "UNREVIEWED", review_id: null, submission_id: null },
      approval: null,
      submission: null,
      parent_manifest_sha256: "b".repeat(64),
      processor_version: "hc-annotated-delivery-queue/1",
      reader_version: "lerobot/0.6.1",
      archive_format: "independent_episode_zip/1",
      includes_collected_depth: true,
    },
    status: "READY",
    phase: "completed",
    generation: 2,
    attempts: 2,
    artifact_sha256: "e".repeat(64),
    size_bytes: 1000,
    manifest_sha256: "f".repeat(64),
    error_code: null,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
  };
  return { scope, task, history, revision, job };
}
