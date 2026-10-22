// AUTO-GENERATED — DO NOT EDIT
// Source: production-composed runtime create_app(...).openapi()
// Runtime-OpenAPI-SHA256: e4e8cf037b3d44229f61b82ee7f10f17c80efe8eb3c1101965b6dbb125a41953
// Response projections: unknown object fields are stripped; defaults are not filled.
import { z } from "zod";
import type { components } from "./platform";

export const CollectionReleaseExclusionWire = z.object({
  episode_id: z.string(),
  reason: z
    .string()
    .pipe(
      z.literal([
        "ANNOTATION_NOT_APPROVED",
        "ANNOTATION_TASK_SELECTION_REQUIRED",
        "NOT_SELECTED",
      ]),
    ),
}) satisfies z.ZodType<components["schemas"]["CollectionReleaseExclusion"]>;
export const CollectionReleasePreflightWire = z.object({
  annotation_task_options: z.record(z.string(), z.array(z.string())),
  collection_manifest_sha256: z
    .string()
    .regex(new RegExp("^[0-9a-f]{64}$", "u")),
  collection_version_id: z.string(),
  content_hash: z.string().regex(new RegExp("^[0-9a-f]{64}$", "u")),
  dataset_id: z.string(),
  dataset_version: z.string(),
  eligible_episode_ids: z.array(z.string()),
  excluded_episodes: z.array(CollectionReleaseExclusionWire),
}) satisfies z.ZodType<components["schemas"]["CollectionReleasePreflight"]>;
export const CollectionReleaseWire = z.object({
  collection_manifest_sha256: z
    .string()
    .regex(new RegExp("^[0-9a-f]{64}$", "u")),
  collection_version_id: z.string(),
  content_hash: z.string().regex(new RegExp("^[0-9a-f]{64}$", "u")),
  created_at: z.iso.datetime({ offset: true }),
  dataset_id: z.string(),
  dataset_version: z.string(),
  document_sha256: z.string().regex(new RegExp("^[0-9a-f]{64}$", "u")),
  document_size: z.number().int().max(67108864).gt(0),
  eligible_episode_ids: z.array(z.string()).min(1),
  excluded_episodes: z.array(CollectionReleaseExclusionWire),
  format: z.string().pipe(z.literal("hc-collection-release/1")),
  version_id: z
    .string()
    .regex(new RegExp("^version_release_[0-9a-f]{32}$", "u")),
}) satisfies z.ZodType<components["schemas"]["CollectionRelease"]>;
export const LeRobotCapabilityReasonWire = z.object({
  code: z.string(),
  detail: z.string(),
  stage: z.string().pipe(z.literal(["STORAGE", "PROCESSING"])),
}) satisfies z.ZodType<components["schemas"]["LeRobotCapabilityReason"]>;
export const LeRobotCapabilitiesWire = z.object({
  capability_revision: z.string(),
  info_sha256: z.string().regex(new RegExp("^[a-f0-9]{64}$", "u")),
  output_storage_format: z
    .union([z.string().pipe(z.literal("lerobot_v3")), z.null()])
    .optional(),
  processing_supported: z.boolean(),
  profile_id: z.union([z.string(), z.null()]).optional(),
  reasons: z.array(LeRobotCapabilityReasonWire),
  schema_version: z.string().pipe(z.literal("lerobot-capabilities/v1")),
  storage_supported: z.boolean(),
  validation_scope: z.string().pipe(z.literal("DECLARED_METADATA")),
}) satisfies z.ZodType<components["schemas"]["LeRobotCapabilities"]>;
export const UploadAlignedMediaTargetV1Wire = z.object({
  annotation_task_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  dataset_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  dataset_version: z.number().int().min(1),
  end_step: z.number().int().gt(0),
  fps: z.number().int().pipe(z.literal(30)),
  project_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  rollout_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  schema_version: z.string().pipe(z.literal("upload-aligned-media-target/v1")),
  start_step: z.number().int().min(0),
}) satisfies z.ZodType<components["schemas"]["UploadAlignedMediaTargetV1"]>;
export const UploadEpisodeTargetV1Wire = z.object({
  annotation_task_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  content_hash: z.string().regex(new RegExp("^[a-f0-9]{64}$", "u")),
  data_version_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  dataset_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  episode_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  fps: z.number().gt(0),
  frame_count: z.number().int().gt(0),
  project_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  region_code: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  rollout_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  schema_version: z.string().pipe(z.literal("upload-episode-target/v1")),
}) satisfies z.ZodType<components["schemas"]["UploadEpisodeTargetV1"]>;
export const UploadProcessingStateWire = z
  .string()
  .pipe(
    z.literal([
      "PENDING",
      "RUNNING",
      "SUCCEEDED",
      "TECHNICAL_FAILED",
      "QUALITY_RISK",
      "QUALITY_REJECTED",
      "CANCELLED",
    ]),
  ) satisfies z.ZodType<components["schemas"]["UploadProcessingState"]>;
export const DatasetIngestViewerTargetWire = z.object({
  dataset_id: z
    .string()
    .regex(new RegExp("^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$", "u")),
  episode_id: z
    .string()
    .regex(new RegExp("^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$", "u")),
  revision_id: z
    .string()
    .regex(new RegExp("^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$", "u")),
  schema_version: z.string().pipe(z.literal("dataset-ingest-viewer-target/v1")),
  version_id: z
    .string()
    .regex(new RegExp("^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$", "u")),
}) satisfies z.ZodType<components["schemas"]["DatasetIngestViewerTarget"]>;
export const UploadProcessingStatusV1Wire = z.object({
  aligned_media: z.union([UploadAlignedMediaTargetV1Wire, z.null()]),
  attempt: z.number().int().min(0),
  episode: z.union([UploadEpisodeTargetV1Wire, z.null()]).optional(),
  error_code: z.union([
    z.string().max(128).regex(new RegExp("^[A-Z0-9_]+$", "u")),
    z.null(),
  ]),
  rollout_id: z
    .string()
    .min(1)
    .max(128)
    .regex(new RegExp("^[A-Za-z0-9._-]+$", "u")),
  schema_version: z.string().pipe(z.literal("upload-processing-status/v1")),
  session_id: z.string().uuid(),
  stage: z.string().min(1).max(128),
  status: UploadProcessingStateWire,
  updated_at: z.iso.datetime({ offset: true }),
  viewer: z.union([DatasetIngestViewerTargetWire, z.null()]),
  workflow_id: z.string().min(1),
}) satisfies z.ZodType<components["schemas"]["UploadProcessingStatusV1"]>;
