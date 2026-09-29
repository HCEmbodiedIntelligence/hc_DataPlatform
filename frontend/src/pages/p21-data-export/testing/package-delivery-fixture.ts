import { completeDeliveryFixture } from "../../p06-dataset-detail/testing/complete-delivery-fixture";
import { deliveryFixture } from "../../p08-data-annotation/testing/delivery-fixture";
import type { PackageJob } from "../package-deliveries";

export function packageDeliveryFixture(annotated = false) {
  const base = completeDeliveryFixture(),
    annotation = deliveryFixture();
  const { scope, data } = annotated
    ? { scope: annotation.scope, data: annotation.job.plan.binding.data }
    : base;
  const job: PackageJob = {
    ...base.job,
    plan: {
      format: "hc-collection-delivery-plan/1",
      modalities: "rgbd",
      archive_format: "lerobot_collection_zip/1",
      annotation_selection: "per_episode_fixed_selection",
      contract_sha256: "f".repeat(64),
      processor_version: "hc-lerobot-collection/1",
      archive_version: "hc-collection-archive/1",
      reader_version: "lerobot/0.6.1",
      members: [
        {
          data,
          manifest_sha256: "b".repeat(64),
          annotation: annotated ? structuredClone(annotation.job.plan) : null,
          selected_depth_profiles: {
            "observation.images.head_depth": "e".repeat(64),
          },
          excluded_depth_features: [],
        },
      ],
    },
  };
  return {
    scope,
    data,
    job,
    annotation,
    candidates: {
      dataset_id: data.identity.dataset_id,
      collection_version_id: "cv_fixed",
      version: 1,
      items: [data],
      total: 1,
      legacy_episode_count: 0,
      next_offset: null,
    },
  };
}
