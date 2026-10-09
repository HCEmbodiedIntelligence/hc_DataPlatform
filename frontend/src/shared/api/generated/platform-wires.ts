// AUTO-GENERATED — DO NOT EDIT
// Source: production-composed runtime create_app(...).openapi()
// Runtime-OpenAPI-SHA256: 40ce3f4fde3e763a50b8b39ac3dfb1a057581569ed891d80139d1aea51c21b13
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
