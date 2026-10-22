import { z } from "zod";
import type { components } from "../../shared/api/generated/platform";

// Runtime response projections are checked against the generated service contract.
export const formatSchema = z.enum(["MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"]);
export type SourceFormat = z.infer<typeof formatSchema>;
export const featureSchema = z.object({
  dtype: z.string().min(1),
  shape: z.array(z.number().int().positive()).min(1),
  names: z
    .union([z.array(z.string()), z.record(z.string(), z.array(z.string()))])
    .nullable()
    .default(null),
  units: z.array(z.string()).default([]),
  role: z.enum([
    "state",
    "action",
    "rgb",
    "depth",
    "numeric",
    "validity",
    "timestamp",
  ]),
  required: z.boolean().default(true),
  missing: z.literal("presence_and_validity").default("presence_and_validity"),
  validity_feature: z.string().nullable().default(null),
  media: z
    .record(z.string(), z.union([z.string(), z.number(), z.boolean()]))
    .default({}),
});
export type Feature = z.infer<typeof featureSchema>;
export const outputSchema = z.object({
  algorithm: z.literal("capture-structure/v1"),
  frequency_hz: z.number().positive().max(1000),
  frequency_policy: z.literal("PRESERVE"),
  time_source: z.enum(["FRAME_TIMESTAMP", "STATE_LOG_TIME", "RGB_LOG_TIME"]),
  features: z.record(z.string(), featureSchema),
});
const draftOutputSchema = outputSchema.extend({
  frequency_hz: z.number().positive().max(1000).nullable().default(null),
});
export const mcapConfigSchema = z.object({
  collection_mode: z.enum(["RGB", "JOINT_ONLY"]),
  time_source: z.enum(["RGB_LOG_TIME", "STATE_LOG_TIME"]),
  cameras: z.array(
    z.object({
      camera_id: z.string(),
      topic: z.string(),
      message_type: z.string(),
      kind: z.enum(["rgb", "depth"]),
      required: z.boolean(),
    }),
  ),
  state_topic: z.string().nullable(),
  action_topic: z.string().nullable(),
  primary_camera: z.string().nullable(),
  joint_names: z.array(z.string()),
  joint_units: z.array(z.string()),
  state_joint_names: z.array(z.string()).default([]),
  state_joint_units: z.array(z.string()).default([]),
  action_joint_names: z.array(z.string()).default([]),
  action_joint_units: z.array(z.string()).default([]),
  depth_scale_m_per_unit: z.number().nullable(),
  depth_unit_evidence: z.string().nullable(),
  derive_action_from_next_state: z.boolean(),
  maximum_time_error_ms: z.number(),
  fps: z.number().int().positive().max(240).nullable().default(null),
  semantic_output: draftOutputSchema.nullable().optional(),
  numeric_sources: z
    .record(
      z.string(),
      z.object({
        topic: z.string().min(1).max(512),
        message_type: z.string().min(1).max(256),
        fields: z.array(z.string()).min(1).max(256),
      }),
    )
    .optional(),
  alignment_policy: z.enum(["PRESERVE", "FIXED_GRID"]).default("PRESERVE"),
});
export type McapConfig = z.infer<typeof mcapConfigSchema>;
export const adapterSchema = z.discriminatedUnion("source_format", [
  z.object({
    source_format: z.literal("CAPTURE_BUNDLE"),
    parser_version: z.literal("capture-bundle/1"),
    configuration: mcapConfigSchema,
  }),
  z.object({
    source_format: z.literal("MCAP"),
    parser_version: z.literal("native-mcap-recording/2"),
    configuration: mcapConfigSchema,
  }),
  z.object({
    source_format: z.literal("LEROBOT_V3"),
    parser_version: z.literal("lerobot-v3/1"),
    codebase_version: z.literal("v3.0"),
    feature_sources: z.record(z.string(), z.string()),
  }),
]);
export const documentSchema = z.object({
  input_mode: z.enum(["EXPLICIT", "AUTO"]).default("EXPLICIT"),
  name: z.string().min(1).max(120),
  description: z.string(),
  software: z.string().max(120).default(""),
  model_version_ids: z.array(z.string()),
  adapters: z.array(adapterSchema).min(1),
  output: draftOutputSchema,
  quality_policy: z.record(z.string(), z.string()),
}) satisfies z.ZodType<components["schemas"]["CaptureDraftDefinition"]>;
export type SchemeDocument = z.infer<typeof documentSchema>;
export type SchemeDraft = SchemeDocument & { expected_revision: number };
export const referenceSchema = z.object({
  scheme_id: z.string(),
  version: z.number().int().positive(),
  content_sha256: z.string().regex(/^[a-f0-9]{64}$/),
});
export type VersionReference = z.infer<typeof referenceSchema>;
export const sampleSchema = z.object({
  project_id: z.string().min(1),
  region_code: z.string().min(1),
  raw_source_id: z.string().min(1),
  source_path: z.string().min(1),
  episode_index: z.number().int().nonnegative(),
  source_format: formatSchema,
});
export type Sample = z.infer<typeof sampleSchema>;
export const differenceSchema = z.object({
  path: z.string(),
  expected: z.unknown(),
  actual: z.unknown(),
});
export type Difference = z.infer<typeof differenceSchema>;
export const evidenceSchema = z.object({
  status: z.enum(["PASSED", "FAILED"]),
  source_format: formatSchema,
  revision: z.number().nullable().optional(),
  parser_version: z.string().optional(),
  draft_sha256: z.string().nullable().optional(),
  sample_sha256: z.string().optional(),
  validated_at: z.string().optional(),
  differences: z.array(differenceSchema).default([]),
  presence: z.record(z.string(), z.boolean()).default({}),
  error_code: z.string().nullable().optional(),
  error_detail: z.string().nullable().optional(),
  sample: sampleSchema.nullable().optional(),
});
export type Evidence = z.infer<typeof evidenceSchema>;
export const versionSchema = documentSchema.extend({
  output: outputSchema,
  adapters: z.array(
    z.discriminatedUnion("source_format", [
      adapterSchema.options[1].extend({
        configuration: mcapConfigSchema.extend({
          fps: z.number().int().positive().max(240),
        }),
      }),
      adapterSchema.options[2],
      adapterSchema.options[0].extend({
        configuration: mcapConfigSchema.extend({
          fps: z.number().int().positive().max(240),
        }),
      }),
    ]),
  ),
  ...referenceSchema.shape,
  draft_revision: z.number(),
  published_at: z.string(),
  output_contract_sha256: z.string(),
  validations: z.record(z.string(), evidenceSchema),
});
export type SchemeVersion = z.infer<typeof versionSchema>;
export const summarySchema = z.object({
  scheme_id: z.string(),
  revision: z.number(),
  draft: documentSchema,
  latest_version: z.number().nullable(),
  archived: z.boolean(),
  instance_count: z.number(),
  task_count: z.number(),
});
export type SchemeSummary = z.infer<typeof summarySchema>;
export const detailSchema = summarySchema
  .omit({ instance_count: true, task_count: true })
  .extend({
    validations: z.array(evidenceSchema),
    versions: z.array(versionSchema),
  });
export type SchemeDetail = z.infer<typeof detailSchema>;
export const mappingSchema = z.object({
  sources: z.record(z.string(), z.record(z.string(), z.string())),
});
export type DeviceMapping = z.infer<typeof mappingSchema>;
export const linkSchema = z.object({
  scheme_id: z.string(),
  revision: z.number(),
  scheme_version: z.number(),
  mapping_version: z.number(),
  is_default: z.boolean(),
  active: z.boolean(),
  archived: z.boolean(),
  status: z.enum(["READY", "MAPPING_UNVERIFIED"]),
  validated_formats: z.array(formatSchema),
  validations: z.array(evidenceSchema),
  scheme: versionSchema,
  mapping: z.object({
    scheme: referenceSchema,
    mapping: mappingSchema,
    mapping_version: z.number(),
    content_sha256: z.string(),
  }),
});
export type SchemeLink = z.infer<typeof linkSchema>;
export interface AssociateCommand {
  expected_revision: number;
  scheme: VersionReference;
  mapping: DeviceMapping;
  make_default: boolean;
}
export const referencesSchema = z.object({
  instances: z.array(
    z.object({
      robot_id: z.string(),
      version: z.number(),
      mapping_version: z.number(),
      active: z.boolean(),
      is_default: z.boolean(),
    }),
  ),
  tasks: z.array(
    z.object({
      project_id: z.string(),
      collection_task_id: z.string(),
      revision: z.number(),
      version: z.number(),
      locked_at: z.string().nullable(),
    }),
  ),
});
export interface SetTaskPlan {
  expected_revision: number;
  scheme: VersionReference;
}
export const taskPlanSchema = z.object({
  organization_id: z.string(),
  project_id: z.string(),
  region_code: z.string(),
  collection_task_id: z.string(),
  dataset_id: z.string(),
  revision: z.number().int().positive(),
  scheme: referenceSchema,
  participants: z.array(
    z.object({
      robot_id: z.string(),
      mapping_version: z.number().int().positive(),
      mapping_sha256: z.string(),
    }),
  ),
  output: outputSchema,
  locked_at: z.string().nullable(),
}) satisfies z.ZodType<
  Pick<
    components["schemas"]["CaptureTaskPlan"],
    | "organization_id"
    | "project_id"
    | "region_code"
    | "collection_task_id"
    | "dataset_id"
    | "revision"
    | "scheme"
    | "participants"
    | "output"
    | "locked_at"
  >
>;
export type TaskPlan = z.infer<typeof taskPlanSchema>;
export function versionReference(version: VersionReference): VersionReference {
  return {
    scheme_id: version.scheme_id,
    version: version.version,
    content_sha256: version.content_sha256,
  };
}
