import type { EpisodeOverview } from "../episode-detail";
import { qualityFixture } from "./quality-fixture";

export function episodeDetailFixture() {
  const { scope, data, quality } = qualityFixture();
  const view: EpisodeOverview = {
    data,
    display_name: "抓取演示",
    display_name_origin: "SOURCE",
    task: {
      task_id: "task",
      instructions: ["将物体放入收纳盒"],
      requirements: null,
      requirements_status: "NOT_RECORDED",
    },
    robot_id: "robot",
    recording_date: "2026-09-29",
    duration_seconds: data.frame_count / 30,
    frequency_hz: 30,
    camera_stream_count: 0,
    physical_camera_count: null,
    physical_camera_count_reason: "PHYSICAL_CAMERA_PAIRING_NOT_RECORDED",
    source: {
      document: {},
      parent_recording_id: null,
      parent_duration_ns: null,
      window: {},
      window_clock: "NOT_RECORDED",
      frame_mapping_files: [],
    },
    quality,
    data_review_status: "NOT_REVIEWED",
    latest_data_review: null,
    annotation: {
      availability: "NOT_CREATED",
      task_options: [],
      task_options_has_more: false,
      task_id: null,
      status: null,
      state_version: null,
      current_revision: null,
      latest_submission: null,
      latest_approval: null,
      latest_review: null,
      current_submission_id: null,
      current_approved_review_id: null,
      has_unsubmitted_changes: null,
      revision_files: [],
      historical_migrations: [],
    },
    subtasks: {
      status: "NOT_SPECIFIED",
      delivery_id: null,
      revision: null,
      track: null,
      segment_count: null,
    },
    cameras: [],
    files: {
      data,
      total: 1,
      next_after: null,
      items: [
        {
          file_id: "a".repeat(64),
          path: "meta/info.json",
          size: 123,
          sha256: "b".repeat(64),
          media_type: "application/json",
          category: "METADATA",
          availability: "AVAILABLE",
          integrity: "MANIFEST_BOUND",
          download: {
            action: "DOWNLOAD_FILE",
            allowed: true,
            required_capabilities: ["export.download"],
            denied_reason: null,
          },
        },
      ],
    },
    deliveries: [],
    deliveries_has_more: false,
    package_status: "COMMITTED",
    allowed_actions: [],
    observed_at: "2026-09-29T12:00:00Z",
  };
  return { scope, data, view };
}

export function episodeMigrationFixture() {
  const f = episodeDetailFixture();
  const mapping = {
    mapping_id: `am_${"c".repeat(64)}`,
    source_task_id: "historical-task",
    source_revision: 2,
    source_revision_content_hash: "d".repeat(64),
    source_data: {
      identity: f.data.identity,
      storage_format: "lance" as const,
      data_version_id: "historical-data",
      content_hash: "e".repeat(64),
      schema_snapshot_id: f.data.schema_snapshot_id,
      frame_count: f.data.frame_count,
      dataset_version: 132,
      dataset_uri: "s3://isolated/history.lance",
      lance_version: 132,
    },
    source_status: "APPROVED",
    source_submission_count: 1,
    source_review_count: 1,
    target_revision: 1 as const,
    target_revision_content_hash: "b".repeat(64),
    document_sha256: "f".repeat(64),
    document_size: 200,
    review_automatically_transferred: false as const,
    download: {
      action: "DOWNLOAD_ANNOTATION_MIGRATION",
      allowed: true,
      required_capabilities: ["annotation_task.read", "export.download"],
      denied_reason: null,
    },
  };
  f.view.annotation = {
    ...f.view.annotation,
    availability: "AVAILABLE",
    task_id: "new-task",
    status: "DRAFT",
    state_version: 1,
    current_revision: {
      task_id: "new-task",
      revision: 1,
      content_hash: mapping.target_revision_content_hash,
      author_id: "migration-operator",
      created_at: f.view.observed_at,
    },
    has_unsubmitted_changes: true,
    historical_migrations: [mapping],
  };
  return { ...f, mapping };
}
