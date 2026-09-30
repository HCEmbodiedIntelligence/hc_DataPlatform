import type { CompleteDeliveryJob } from "../../../features/datasets/episodes/complete-deliveries";
import { episodeDetailFixture } from "./episode-detail-fixture";

export function completeDeliveryFixture() {
  const { scope, data } = episodeDetailFixture();
  const job: CompleteDeliveryJob = {
    job_id: "ed_" + "a".repeat(64),
    plan: {
      format: "hc-episode-delivery-plan/1",
      members: [
        {
          data,
          manifest_sha256: "b".repeat(64),
          root: "episode",
          file_count: 3,
          byte_count: 100,
        },
      ],
      archive_format: "independent_episode_zip/1",
      modalities: "all_collected",
      annotation_selection: "package_contents_only",
      processor_version: "hc-episode-archive/1",
      reader_version: "lerobot/0.6.1",
    },
    status: "READY",
    phase: "completed",
    generation: 1,
    attempts: 1,
    processed_files: 4,
    processed_bytes: 200,
    total_files: 4,
    total_bytes: 200,
    uploaded_bytes: 300,
    artifact_sha256: "c".repeat(64),
    size_bytes: 300,
    error_code: null,
    created_at: "2026-09-29T14:00:00Z",
    updated_at: "2026-09-29T14:00:01Z",
  };
  return { scope, data, job };
}
