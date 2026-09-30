import type { TagEpisodeJob } from "../tag-episodes";
import { isDatasetId } from "../../../entities/dataset";
import { isDatasetVersionId } from "../../../entities/dataset-version";
import { isEpisodeId } from "../../../entities/episode";
import { deliveryFixture } from "./delivery-fixture";

export function tagEpisodeFixture() {
  const f = deliveryFixture();
  const parent = f.job.plan.binding.data;
  // Shared annotation fixtures predate the product route ID contract.
  // Every data_ref in this fixture points at this same parent object.
  parent.identity.dataset_id = "dataset_tag_fixture";
  f.task.dataset_id = parent.identity.dataset_id;
  const child = {
    ...parent,
    identity: {
      ...parent.identity,
      episode_id: "episode_tag_child",
      rollout_id: "rollout-tag-child",
    },
    data_version_id: "dv_" + "e".repeat(64),
    frame_count: 2,
    content_hash: "f".repeat(64),
    package_prefix: "episodes/child",
    version: 1,
  };
  const datasetId = child.identity.dataset_id;
  const episodeId = child.identity.episode_id;
  const versionId = "version_collection-child";
  if (
    !isDatasetId(datasetId) ||
    !isEpisodeId(episodeId) ||
    !isDatasetVersionId(versionId)
  )
    throw new Error("Invalid fixture route identity");
  const job: TagEpisodeJob = {
    job_id: "td_" + "c".repeat(64),
    status: "READY",
    phase: "completed",
    generation: 1,
    attempts: 1,
    selection: {
      parent,
      parent_collection_version_id: "collection-parent",
      task_id: f.task.task_id,
      revision: f.revision.revision,
      revision_content_hash: f.revision.content_hash,
      annotation_file_sha256: "d".repeat(64),
      review_status: "UNREVIEWED",
      annotation_id: "first",
      start_step: 0,
      end_step: 2,
      start_timestamp_ns: "1790583427123456789",
      next_parent_timestamp_ns: "1790583427190123456",
      display_name: "第一段独立数据",
      new_episode_id: child.identity.episode_id,
      new_data_version_id: child.data_version_id,
    },
    committed_data: child,
    collection_version_id: "collection-child",
    viewer_target: {
      schema_version: "dataset-ingest-viewer-target/v1",
      dataset_id: datasetId,
      version_id: versionId,
      episode_id: episodeId,
      revision_id: "revision-child",
    },
    annotation_task_id: "annotation-child",
    error_code: null,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
  };
  return { ...f, job };
}
