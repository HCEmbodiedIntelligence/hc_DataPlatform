import { z } from "zod";

export const episodeDataReferenceWireSchema = z
  .object({
    identity: z
      .object({
        organization_id: z.string().min(1).max(256),
        project_id: z.string().min(1).max(256),
        region_code: z.string().min(1).max(256),
        dataset_id: z.string().min(1).max(256),
        episode_id: z.string().min(1).max(256),
        rollout_id: z.string().min(1).max(256),
      })
      .strict(),
    data_version_id: z.string().min(1).max(256),
    version: z.number().int().positive(),
    content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    storage_format: z.literal("lerobot_v3"),
    schema_snapshot_id: z.string().min(1).max(256),
    package_prefix: z.string().min(1),
    frame_count: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  })
  .strict();

export const episodePackageBindingWireSchema = z
  .object({
    data: episodeDataReferenceWireSchema,
    rollout_id: z.string().min(1).max(256),
    feature_key: z.string().min(1).max(512),
    modality_key: z.string().min(1).max(512),
    value_kind: z
      .enum(["SCALAR", "VECTOR", "POINTCLOUD_XYZ", "EVENT"])
      .nullable(),
    start_step: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    end_step: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.rollout_id !== value.data.identity.rollout_id ||
      value.end_step <= value.start_step ||
      value.end_step > value.data.frame_count
    ) {
      context.addIssue({
        code: "custom",
        message: "package identity/window mismatch",
      });
    }
  });

export type EpisodePackageBinding = z.infer<
  typeof episodePackageBindingWireSchema
>;
