import { z } from "zod";

export const CONTRACT_VERSION = "dataset-version-review.v1alpha1" as const;

export const datasetIdWireSchema = z
  .string()
  .regex(/^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const versionIdWireSchema = z
  .string()
  .regex(/^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/)
  .refine((value) => value !== "version_current" && value !== "version_latest");
export const episodeIdWireSchema = z
  .string()
  .regex(/^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const revisionIdWireSchema = z
  .string()
  .regex(/^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const episodeStreamIdWireSchema = z
  .string()
  .regex(/^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const reviewDecisionIdWireSchema = z
  .string()
  .regex(/^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const reviewFindingIdWireSchema = z
  .string()
  .regex(/^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const draftIdWireSchema = z
  .string()
  .regex(/^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/);
export const decimalIntegerWireSchema = z.string().regex(/^(0|[1-9][0-9]*)$/);
export const decimalNsWireSchema = decimalIntegerWireSchema;
export const isoDateTimeWireSchema = z.string().datetime({ offset: true });
export const sha256WireSchema = z.string().regex(/^[a-f0-9]{64}$/i);
export const etagWireSchema = z.string().min(3).max(256);

export const scopeWireSchema = z
  .object({
    organization_id: z.string().min(1),
    project_id: z.string().min(1),
    region_code: z.string().min(1),
  })
  .strict();

export const blockedReasonWireSchema = z
  .object({
    code: z.string().min(1).max(128),
    message: z.string().min(1).max(1000),
  })
  .strict();

export const allowedActionWireSchema = z
  .object({
    action: z.string().min(1).max(128),
    allowed: z.boolean(),
    blocked_reasons: z.array(blockedReasonWireSchema),
  })
  .strict();

export const pageInfoWireSchema = z
  .object({
    after: z.string().min(1).nullable(),
    before: z.string().min(1).nullable(),
    has_next: z.boolean(),
    has_previous: z.boolean(),
    limit: z.number().int().positive().max(500).optional(),
    total_count: decimalIntegerWireSchema.nullable().optional(),
  })
  .strict();

const actorSummaryWireSchema = z
  .object({
    id: z.string().min(1),
    display_name: z.string().min(1),
  })
  .strict();

export const datasetWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    folder_path: z.array(z.string().min(1).max(128)).max(16),
    name: z.string().min(1).max(256),
    description: z.string().max(4096),
    labels: z.array(z.string().min(1).max(96)).max(64),
    availability: z.string().min(1),
    owner: actorSummaryWireSchema,
    created_at: isoDateTimeWireSchema,
    updated_at: isoDateTimeWireSchema,
    etag: etagWireSchema,
    allowed_actions: z.array(allowedActionWireSchema),
  })
  .strict();

const currentReadyVersionWireSchema = z
  .object({
    version_id: versionIdWireSchema,
    display_version: z.string().min(1).max(64),
    kind: z.string().min(1),
    status: z.literal("READY"),
    published_at: isoDateTimeWireSchema,
    manifest_sha256: sha256WireSchema,
  })
  .strict();

export const datasetListItemWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    folder_path: z.array(z.string().min(1).max(128)).max(16),
    collection_task_id: z.string().min(1).max(128).nullable().optional(),
    name: z.string().min(1).max(256),
    availability: z.string().min(1),
    dataset_created_at: isoDateTimeWireSchema,
    dataset_activity_at: isoDateTimeWireSchema,
    current_version: currentReadyVersionWireSchema.nullable(),
    episode_count: decimalIntegerWireSchema,
    pending_review_version_count: decimalIntegerWireSchema,
    returned_version_count: decimalIntegerWireSchema,
    actionable_draft_count: decimalIntegerWireSchema,
    allowed_actions: z.array(allowedActionWireSchema),
  })
  .strict();

export const datasetSummaryWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_count: decimalIntegerWireSchema,
    episode_count: decimalIntegerWireSchema,
    pending_review_version_count: decimalIntegerWireSchema,
    returned_version_count: decimalIntegerWireSchema,
    actionable_draft_count: decimalIntegerWireSchema,
    normalized_filters: z.record(z.string(), z.unknown()),
  })
  .strict();

const facetValueWireSchema = z
  .object({ value: z.string().min(1), count: decimalIntegerWireSchema })
  .strict();

export const datasetFacetsWireSchema = z
  .object({
    scope: scopeWireSchema,
    normalized_filters: z.record(z.string(), z.unknown()),
    robots: z.array(facetValueWireSchema),
    robot_models: z.array(facetValueWireSchema),
    tasks: z.array(facetValueWireSchema),
    tags: z.array(facetValueWireSchema),
    scenes: z.array(facetValueWireSchema),
    asset_states: z.array(facetValueWireSchema),
    storage_classes: z.array(facetValueWireSchema),
    channels: z.array(facetValueWireSchema),
  })
  .strict();

export const datasetsPageCapabilitiesWireSchema = z
  .object({
    scope: scopeWireSchema,
    authorization_revision: z.string().min(1),
    allowed_actions: z.array(z.string().min(1)),
    blocked_reasons: z.array(blockedReasonWireSchema),
  })
  .strict();

export const datasetListEnvelopeWireSchema = z
  .object({
    items: z.array(datasetListItemWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

export const datasetDetailSummaryWireSchema = z
  .object({
    episode_count: decimalIntegerWireSchema,
    effective_duration_ns: decimalNsWireSchema,
    source_bytes: decimalIntegerWireSchema,
    required_physical_bytes: decimalIntegerWireSchema,
    actual_oss_bytes: decimalIntegerWireSchema.nullable(),
    pending_review_version_count: decimalIntegerWireSchema,
    returned_version_count: decimalIntegerWireSchema,
    actionable_draft_count: decimalIntegerWireSchema,
    calculated_at: isoDateTimeWireSchema,
    calculation_state: z.string().min(1),
  })
  .strict();

export const datasetBootstrapDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset: datasetWireSchema,
    current_ready_version: currentReadyVersionWireSchema.nullable(),
    working_version_id: versionIdWireSchema.nullable().optional(),
    suggested_version_id: versionIdWireSchema.nullable(),
    summary: datasetDetailSummaryWireSchema,
  })
  .strict();

export const publishedDatasetManifestWireSchema = z
  .object({
    schema_version: z.literal("published-dataset-manifest/v1"),
    data_stage: z.enum(["annotated", "dataset"]).optional(),
    project_id: z.string().min(1),
    dataset_id: datasetIdWireSchema,
    dataset_version: z.string().min(1),
    base_lance_version: z.string().min(1),
    created_at: isoDateTimeWireSchema,
    content_hash: sha256WireSchema,
    annotations_uri: z.string().min(1),
    annotations_content_sha256: sha256WireSchema,
    training_manifest_uri: z.string().min(1),
    training_manifest_content_sha256: sha256WireSchema,
    rollouts: z.array(z.unknown()),
    excluded_rollouts: z.array(z.unknown()),
  })
  .strict();

export const responseMetaWireSchema = z
  .object({
    request_id: z.string().min(1),
    trace_id: z.string().min(1),
    correlation_id: z.string().min(1),
    generated_at: isoDateTimeWireSchema,
    as_of: isoDateTimeWireSchema.nullable(),
    projection_version: z.string().min(1),
    event_cursor: z.string().nullable(),
  })
  .strict();

function successEnvelope<T extends z.ZodType>(data: T) {
  return z.object({ data, meta: responseMetaWireSchema }).strict();
}

export const datasetBootstrapWireSchema = successEnvelope(
  datasetBootstrapDataWireSchema,
);
export const datasetSummaryEnvelopeWireSchema = successEnvelope(
  datasetSummaryWireSchema,
);
export const datasetFacetsEnvelopeWireSchema = successEnvelope(
  datasetFacetsWireSchema,
);
export const datasetsPageCapabilitiesEnvelopeWireSchema = successEnvelope(
  datasetsPageCapabilitiesWireSchema,
);

const versionIdentityShape = {
  scope: scopeWireSchema,
  dataset_id: datasetIdWireSchema,
  version_id: versionIdWireSchema,
  display_version: z.string().min(1).max(64),
  kind: z.string().min(1),
  created_at: isoDateTimeWireSchema,
  etag: etagWireSchema,
  version_token: z.string().min(16).max(256),
};

const reviewingVersionWireSchema = z
  .object({
    ...versionIdentityShape,
    status: z.literal("REVIEWING"),
    source_draft_id: draftIdWireSchema,
    delivery_status: z.string().min(1),
    approved_review_decision_id: reviewDecisionIdWireSchema
      .nullable()
      .optional(),
    allowed_actions: z.array(allowedActionWireSchema),
  })
  .strict();

const returnedVersionWireSchema = z
  .object({
    ...versionIdentityShape,
    status: z.literal("RETURNED"),
    source_draft_id: draftIdWireSchema,
    review_decision_id: reviewDecisionIdWireSchema,
    review_finding_ids: z.array(reviewFindingIdWireSchema).min(1),
    successor_draft_id: draftIdWireSchema,
    supersedes_draft_id: draftIdWireSchema,
    returned_from_version_id: versionIdWireSchema,
    returned_from_review_decision_id: reviewDecisionIdWireSchema,
    allowed_actions: z.array(allowedActionWireSchema),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.successor_draft_id === value.supersedes_draft_id) {
      context.addIssue({
        code: "custom",
        path: ["successor_draft_id"],
        message: "successor must be new",
      });
    }
    if (value.returned_from_version_id !== value.version_id) {
      context.addIssue({
        code: "custom",
        path: ["returned_from_version_id"],
        message: "version lineage mismatch",
      });
    }
    if (value.returned_from_review_decision_id !== value.review_decision_id) {
      context.addIssue({
        code: "custom",
        path: ["returned_from_review_decision_id"],
        message: "decision lineage mismatch",
      });
    }
  });

const contentReferenceWireSchema = z
  .object({
    reference_type: z.string().min(1),
    reference_id: z.string().min(1),
    reference_version: z.string().min(1),
    sha256: sha256WireSchema,
  })
  .strict();

export const datasetVersionSchemaSummaryDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    schema_snapshot: contentReferenceWireSchema,
    channel_count: decimalIntegerWireSchema.optional(),
  })
  .strict();

export const datasetVersionSchemaSummaryWireSchema = successEnvelope(
  datasetVersionSchemaSummaryDataWireSchema,
);

export const sourceProvenanceWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    storage_region_code: z.string().min(1).max(64).nullable().optional(),
    provenance_id: z.string().min(1).max(128),
    upload_id: z.string().min(1).max(128),
    source_id: z.string().min(1).max(128).nullable().optional(),
    source_display_name: z.string().min(1).max(256).nullable().optional(),
    source_manifest_id: z.string().min(1).max(128),
    source_manifest_sha256: sha256WireSchema,
    verified_object_set_hash: sha256WireSchema,
    registered_at: isoDateTimeWireSchema,
  })
  .strict();

export const sourceProvenancePageWireSchema = z
  .object({
    items: z.array(sourceProvenanceWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

export const datasetVersionCapacityDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    state: z.enum(["CALCULATING", "PARTIAL", "SETTLED", "FAILED"]),
    source_bytes: decimalIntegerWireSchema.nullable(),
    required_physical_bytes: decimalIntegerWireSchema.nullable(),
    actual_oss_bytes: decimalIntegerWireSchema.nullable(),
    calculated_at: isoDateTimeWireSchema,
    basis_revision: z.string().min(1).max(256),
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.state === "SETTLED" &&
      (value.source_bytes === null ||
        value.required_physical_bytes === null ||
        value.actual_oss_bytes === null)
    ) {
      context.addIssue({
        code: "custom",
        path: ["state"],
        message: "SETTLED capacity requires all facts",
      });
    }
  });

export const datasetVersionCapacityWireSchema = successEnvelope(
  datasetVersionCapacityDataWireSchema,
);

export const versionSchemaDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    snapshot_token: z.string().min(16).max(2048),
    schema_snapshot: contentReferenceWireSchema,
    channel_count: decimalIntegerWireSchema,
    channels: z.array(
      z
        .object({
          channel_id: z.string().min(1).max(128),
          name: z.string().min(1).max(256),
          data_type: z.string().min(1).max(128),
          unit: z.string().max(128).nullable(),
        })
        .strict(),
    ),
  })
  .strict()
  .superRefine((value, context) => {
    if (BigInt(value.channel_count) !== BigInt(value.channels.length)) {
      context.addIssue({
        code: "custom",
        path: ["channel_count"],
        message: "channel count mismatch",
      });
    }
  });
export const versionSchemaWireSchema = successEnvelope(
  versionSchemaDataWireSchema,
);

export const requiredStorageItemWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    object_id: z.string().min(1).max(128),
    role: z.enum([
      "SOURCE",
      "REVISION",
      "INDEX",
      "METADATA",
      "PREVIEW",
      "EXPORT",
    ]),
    size_bytes: decimalIntegerWireSchema,
    reuse: z.enum(["NEW", "REUSED", "SHARED", "UNKNOWN"]),
    protection: z.enum([
      "NONE",
      "RETENTION",
      "LEGAL_HOLD",
      "IMMUTABLE",
      "UNKNOWN",
    ]),
    safe_locator: z.string().max(256).nullable().optional(),
  })
  .strict();

const storagePageShape = <T extends z.ZodType>(item: T) =>
  z
    .object({
      items: z.array(item),
      page_info: pageInfoWireSchema,
      snapshot_at: isoDateTimeWireSchema,
      snapshot_id: z.string().min(1),
      scope: scopeWireSchema,
      request_id: z.string().min(1),
      contract_version: z.literal(CONTRACT_VERSION),
    })
    .strict();

export const requiredStoragePageWireSchema = storagePageShape(
  requiredStorageItemWireSchema,
);

export const operationalInventoryItemWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    inventory_id: z.string().min(1).max(128),
    kind: z.enum(["PREVIEW", "EXPORT", "MATERIALIZATION"]),
    operational_revision: z.string().min(1).max(256),
    status: z.enum([
      "QUEUED",
      "RUNNING",
      "SUCCEEDED",
      "FAILED",
      "EXPIRED",
      "STALE",
    ]),
    size_bytes: decimalIntegerWireSchema,
    job_id: z.string().min(1).max(128).nullable(),
    created_at: isoDateTimeWireSchema,
    completed_at: isoDateTimeWireSchema.nullable(),
  })
  .strict();
export const operationalInventoryPageWireSchema = storagePageShape(
  operationalInventoryItemWireSchema,
);

const contentSnapshotWireSchema = z
  .object({
    content_snapshot_id: z.string().min(1),
    content_snapshot_hash: sha256WireSchema,
    revision_refs: z.array(
      z
        .object({
          episode_id: episodeIdWireSchema,
          revision_id: revisionIdWireSchema,
          ordinal: z.number().int().nonnegative(),
          content_sha256: sha256WireSchema,
        })
        .strict(),
    ),
    schema_ref: contentReferenceWireSchema,
    robot_model_refs: z.array(contentReferenceWireSchema),
    calibration_refs: z.array(contentReferenceWireSchema),
    source_manifest_refs: z.array(contentReferenceWireSchema),
  })
  .strict();

const manifestSummaryWireSchema = z
  .object({
    manifest_id: z.string().min(1),
    format_version: z.string().min(1),
    canonicalization: z.string().min(1),
    sha256: sha256WireSchema,
    entry_count: decimalIntegerWireSchema,
  })
  .strict();

const readyVersionWireSchema = z
  .object({
    ...versionIdentityShape,
    status: z.literal("READY"),
    published_at: isoDateTimeWireSchema,
    content_snapshot: contentSnapshotWireSchema,
    manifest: manifestSummaryWireSchema,
    approved_review_decision_id: reviewDecisionIdWireSchema
      .nullable()
      .optional(),
    allowed_actions: z.array(allowedActionWireSchema),
  })
  .strict();

const unknownVersionWireSchema = z
  .object({
    ...versionIdentityShape,
    status: z
      .string()
      .refine((value) => !["REVIEWING", "RETURNED", "READY"].includes(value)),
    allowed_actions: z.array(allowedActionWireSchema).optional().default([]),
  })
  .passthrough();

export const datasetVersionWireSchema = z.union([
  reviewingVersionWireSchema,
  returnedVersionWireSchema,
  readyVersionWireSchema,
  unknownVersionWireSchema,
]);

export const versionPageEnvelopeWireSchema = z
  .object({
    items: z.array(datasetVersionWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

const revisionSnapshotRefWireSchema = z
  .object({
    episode_id: episodeIdWireSchema,
    revision_id: revisionIdWireSchema,
    ordinal: z.number().int().nonnegative(),
    content_sha256: sha256WireSchema,
  })
  .strict();

export const versionEpisodeListItemWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    episode_id: episodeIdWireSchema,
    storage_region_code: z.string().min(1).max(64).nullable().optional(),
    selected_revision: revisionSnapshotRefWireSchema,
    included: z.boolean(),
    success_state: z.string().min(1),
    task: z.string().nullable(),
    robot_id: z.string().nullable(),
    review_status: z.string().nullable().optional(),
    review_finding_count: decimalIntegerWireSchema.nullable().optional(),
  })
  .strict();

export const episodePageEnvelopeWireSchema = z
  .object({
    items: z.array(versionEpisodeListItemWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

export const episodeRevisionHistoryItemWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    episode_id: episodeIdWireSchema,
    version_id: versionIdWireSchema,
    display_version: z.string().min(1).max(64),
    version_kind: z.enum(["RAW", "CLEANED"]),
    version_status: z.enum(["REVIEWING", "RETURNED", "READY"]),
    version_created_at: isoDateTimeWireSchema,
    version_published_at: isoDateTimeWireSchema.nullable(),
    selected_revision: revisionSnapshotRefWireSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.selected_revision.episode_id !== value.episode_id) {
      context.addIssue({
        code: "custom",
        path: ["selected_revision", "episode_id"],
        message: "selected revision does not belong to the history episode",
      });
    }
  });

export const episodeRevisionHistoryWireSchema = z
  .object({
    items: z.array(episodeRevisionHistoryItemWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

export const versionBootstrapDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    snapshot_token: z.string().min(16).max(2048),
    operational_revision: z.string().min(1).max(256),
    version: datasetVersionWireSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.version.dataset_id !== value.dataset_id ||
      value.version.version_id !== value.version_id
    ) {
      context.addIssue({
        code: "custom",
        path: ["version"],
        message: "path identity mismatch",
      });
    }
  });

export const versionBootstrapWireSchema = successEnvelope(
  versionBootstrapDataWireSchema,
);

export const episodeStreamWireSchema = z
  .object({
    episode_stream_id: episodeStreamIdWireSchema,
    channel_path: z.string().min(1).max(512),
    kind: z.string().min(1).max(64),
    t_start_ns: decimalNsWireSchema,
    t_end_ns: decimalNsWireSchema,
    aligned_media_binding: z
      .object({
        rollout_id: z.string().min(1).max(256),
        dataset_version: z.number().int().positive(),
        artifact_id: z.string().min(1).max(256),
        camera_id: z.string().min(1).max(256),
        fps: z.literal(30),
        start_step: z.number().int().nonnegative(),
        end_step: z.number().int().positive(),
      })
      .strict()
      .nullable()
      .default(null),
    data_binding: z
      .object({
        rollout_id: z.string().min(1).max(256),
        lance_version: z.number().int().positive(),
        modality_key: z.string().min(1).max(512),
        value_kind: z.enum(["SCALAR", "VECTOR", "POINTCLOUD_XYZ", "EVENT"]),
        start_step: z.number().int().nonnegative(),
        end_step: z.number().int().positive(),
      })
      .strict()
      .nullable()
      .default(null),
  })
  .strict()
  .superRefine((value, context) => {
    if (BigInt(value.t_start_ns) >= BigInt(value.t_end_ns)) {
      context.addIssue({
        code: "custom",
        path: ["t_end_ns"],
        message: "stream range must be non-empty",
      });
    }
    if (
      value.aligned_media_binding &&
      value.aligned_media_binding.end_step <=
        value.aligned_media_binding.start_step
    ) {
      context.addIssue({
        code: "custom",
        path: ["aligned_media_binding", "end_step"],
        message: "aligned media step window must be non-empty",
      });
    }
    if (
      value.aligned_media_binding &&
      !["VIDEO", "RGB", "RGB_VIDEO", "DEPTH"].includes(
        value.kind.trim().toUpperCase(),
      )
    ) {
      context.addIssue({
        code: "custom",
        path: ["aligned_media_binding"],
        message:
          "aligned media binding is only valid for RGB or depth camera streams",
      });
    }
    if (
      value.data_binding &&
      value.data_binding.end_step <= value.data_binding.start_step
    ) {
      context.addIssue({
        code: "custom",
        path: ["data_binding", "end_step"],
        message: "data step window must be non-empty",
      });
    }
    const streamKind = value.kind.trim().toUpperCase();
    if (
      value.data_binding &&
      ![
        "POINTCLOUD",
        "JOINT_STATE",
        "ACTION",
        "FORCE",
        "POSE",
        "IMU",
        "TACTILE",
        "EVENT",
      ].includes(streamKind)
    ) {
      context.addIssue({
        code: "custom",
        path: ["data_binding"],
        message: "data binding is only valid for supported non-camera streams",
      });
    }
    if (
      value.data_binding &&
      streamKind === "POINTCLOUD" &&
      value.data_binding.value_kind !== "POINTCLOUD_XYZ"
    ) {
      context.addIssue({
        code: "custom",
        path: ["data_binding", "value_kind"],
        message: "pointcloud data binding must use POINTCLOUD_XYZ",
      });
    }
    if (
      value.data_binding &&
      streamKind === "EVENT" &&
      value.data_binding.value_kind !== "EVENT"
    ) {
      context.addIssue({
        code: "custom",
        path: ["data_binding", "value_kind"],
        message: "event data binding must use EVENT",
      });
    }
    if (
      value.data_binding &&
      !["POINTCLOUD", "EVENT"].includes(streamKind) &&
      !["SCALAR", "VECTOR"].includes(value.data_binding.value_kind)
    ) {
      context.addIssue({
        code: "custom",
        path: ["data_binding", "value_kind"],
        message: "numeric data binding must use SCALAR or VECTOR",
      });
    }
  });

export const episodeRevisionDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    episode_id: episodeIdWireSchema,
    revision_id: revisionIdWireSchema,
    ordinal: z.number().int().nonnegative(),
    content_sha256: sha256WireSchema,
    started_at_ns: decimalNsWireSchema,
    duration_ns: decimalNsWireSchema,
    streams: z.array(episodeStreamWireSchema).min(1),
  })
  .strict();

export const episodeRevisionWireSchema = successEnvelope(
  episodeRevisionDataWireSchema,
);

const severityWireSchema = z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);
const findingCatalogWireSchema = z
  .object({
    version: z.string().min(1).max(128),
    finding_types: z
      .array(
        z
          .object({
            code: z.string().regex(/^[A-Z][A-Z0-9_:-]*$/),
            label: z.string().min(1).max(256),
            allowed_severities: z.array(severityWireSchema).min(1),
          })
          .strict(),
      )
      .min(1),
    severities: z
      .array(
        z
          .object({
            code: severityWireSchema,
            label: z.string().min(1).max(256),
            rank: z.number().int().positive(),
          })
          .strict(),
      )
      .min(1),
    note_min_length: z.number().int().positive(),
    note_max_length: z.number().int().positive(),
  })
  .strict();

export const reviewChecksDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    dataset_id: datasetIdWireSchema,
    output_version_id: versionIdWireSchema,
    source_draft_id: draftIdWireSchema,
    expected_status: z.literal("REVIEWING"),
    review_token: z.string().min(32).max(2048),
    review_token_expires_at: isoDateTimeWireSchema,
    version_token: z.string().min(16).max(256),
    blockers: z.array(blockedReasonWireSchema),
    finding_catalog: findingCatalogWireSchema,
    eligible_targets: z
      .array(
        z
          .object({
            output_revision_id: revisionIdWireSchema,
            episode_id: episodeIdWireSchema,
            streams: z.array(episodeStreamWireSchema).min(1),
          })
          .strict(),
      )
      .min(1),
  })
  .strict();

export const reviewChecksWireSchema = successEnvelope(
  reviewChecksDataWireSchema,
);

export const createDatasetRequestWireSchema = z
  .object({
    name: z.string().trim().min(1).max(256),
    description: z.string().max(4096),
    labels: z.array(z.string().min(1).max(96)).max(64),
    folder_path: z.array(z.string().min(1).max(128)).max(16),
  })
  .strict();

export const datasetEnvelopeWireSchema = successEnvelope(datasetWireSchema);

export const returnReviewCommandWireSchema = z
  .object({
    expected_status: z.literal("REVIEWING"),
    review_token: z.string().min(32).max(2048),
    finding_catalog_version: z.string().min(1).max(128),
    findings: z
      .array(
        z
          .object({
            output_revision_id: revisionIdWireSchema,
            episode_stream_id: episodeStreamIdWireSchema,
            start_ns: decimalNsWireSchema,
            end_ns: decimalNsWireSchema,
            finding_type: z
              .string()
              .regex(/^[A-Z][A-Z0-9_:-]*$/)
              .max(96),
            severity: severityWireSchema,
            note: z.string().trim().min(1).max(8192),
          })
          .strict()
          .superRefine((value, context) => {
            if (BigInt(value.start_ns) >= BigInt(value.end_ns)) {
              context.addIssue({
                code: "custom",
                path: ["end_ns"],
                message: "range must be [start,end)",
              });
            }
          }),
      )
      .min(1)
      .max(500),
  })
  .strict();

export const approveReviewCommandWireSchema = z
  .object({
    expected_status: z.literal("REVIEWING"),
    review_token: z.string().min(32).max(2048),
  })
  .strict();

export const manifestEntryWireSchema = z
  .object({
    entry_id: z.string().min(1),
    episode_id: episodeIdWireSchema,
    revision_id: revisionIdWireSchema,
    role: z.enum(["SOURCE", "REVISION", "INDEX", "METADATA"]),
    size_bytes: decimalIntegerWireSchema,
    sha256: sha256WireSchema,
    safe_locator: z.string().max(256).nullable().optional(),
  })
  .strict();

export const versionManifestWireSchema = z
  .object({
    items: z.array(manifestEntryWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    snapshot_id: z.string().min(1),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
    dataset_id: datasetIdWireSchema,
    version_id: versionIdWireSchema,
    content_snapshot_id: z.string().min(1),
    manifest: manifestSummaryWireSchema,
  })
  .strict();

export const jobAcceptedWireSchema = z
  .object({
    job: z.object({ job_id: z.string().min(1) }).passthrough(),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict();

export const reviewFindingWireSchema = z
  .object({
    id: reviewFindingIdWireSchema,
    output_revision_id: revisionIdWireSchema,
    episode_stream_id: episodeStreamIdWireSchema,
    start_ns: decimalNsWireSchema,
    end_ns: decimalNsWireSchema,
    finding_type: z
      .string()
      .regex(/^[A-Z][A-Z0-9_:-]*$/)
      .max(96),
    severity: z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
    note: z.string().trim().min(1).max(8192),
    immutable: z.literal(true),
    created_at: isoDateTimeWireSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (BigInt(value.start_ns) >= BigInt(value.end_ns)) {
      context.addIssue({
        code: "custom",
        path: ["end_ns"],
        message: "range must be [start,end)",
      });
    }
  });

export const reviewDecisionWireSchema = z
  .object({
    id: reviewDecisionIdWireSchema,
    output_version_id: versionIdWireSchema,
    decision: z.enum(["APPROVED", "RETURNED"]),
    immutable: z.literal(true),
    created_at: isoDateTimeWireSchema,
  })
  .strict();

export const approveReviewResultWireSchema = z
  .object({
    review_decision: reviewDecisionWireSchema.extend({
      decision: z.literal("APPROVED"),
    }),
    output_version: z
      .object({
        id: versionIdWireSchema,
        status: z.literal("REVIEWING"),
        version_token: z.string().min(16),
      })
      .strict(),
    job: z.object({ job_id: z.string().min(1) }).passthrough(),
    scope: scopeWireSchema,
    request_id: z.string().min(1),
    contract_version: z.literal(CONTRACT_VERSION),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.review_decision.output_version_id !== value.output_version.id) {
      context.addIssue({
        code: "custom",
        path: ["output_version", "id"],
        message: "approved version identity mismatch",
      });
    }
  });

export const returnReviewResultDataWireSchema = z
  .object({
    scope: scopeWireSchema,
    review_decision: reviewDecisionWireSchema.extend({
      decision: z.literal("RETURNED"),
    }),
    findings: z.array(reviewFindingWireSchema).min(1),
    review_finding_ids: z.array(reviewFindingIdWireSchema).min(1),
    output_version: z
      .object({
        id: versionIdWireSchema,
        status: z.literal("RETURNED"),
        version_token: z.string().min(16),
      })
      .strict(),
    successor_draft_id: draftIdWireSchema,
    supersedes_draft_id: draftIdWireSchema,
    returned_from_version_id: versionIdWireSchema,
    returned_from_review_decision_id: reviewDecisionIdWireSchema,
  })
  .strict()
  .superRefine((value, context) => {
    const ids = value.findings.map((finding) => finding.id);
    if (
      new Set(ids).size !== ids.length ||
      new Set(value.review_finding_ids).size !== value.review_finding_ids.length
    ) {
      context.addIssue({
        code: "custom",
        path: ["review_finding_ids"],
        message: "finding IDs must be unique",
      });
    }
    if (new Set(ids).size !== ids.length) {
      context.addIssue({
        code: "custom",
        path: ["findings"],
        message: "finding IDs must be unique",
      });
    }
    if (
      ids.length !== value.review_finding_ids.length ||
      ids.some((id, index) => id !== value.review_finding_ids[index])
    ) {
      context.addIssue({
        code: "custom",
        path: ["review_finding_ids"],
        message: "finding order mismatch",
      });
    }
    if (
      value.output_version.id !== value.returned_from_version_id ||
      value.returned_from_version_id !== value.review_decision.output_version_id
    ) {
      context.addIssue({
        code: "custom",
        path: ["returned_from_version_id"],
        message: "version mismatch",
      });
    }
    if (value.returned_from_review_decision_id !== value.review_decision.id) {
      context.addIssue({
        code: "custom",
        path: ["returned_from_review_decision_id"],
        message: "decision mismatch",
      });
    }
    if (value.successor_draft_id === value.supersedes_draft_id) {
      context.addIssue({
        code: "custom",
        path: ["successor_draft_id"],
        message: "successor must be new",
      });
    }
  });

export const returnReviewResultWireSchema = successEnvelope(
  returnReviewResultDataWireSchema,
);

const deletionCheckWireSchema = z
  .object({
    check_type: z.enum([
      "ACTIVE_REFERENCES",
      "RETENTION",
      "LEGAL_HOLD",
      "PERMISSION",
      "CONCURRENT_JOBS",
      "CURRENT_READY",
      "AUDIT_PROTECTION",
    ]),
    passed: z.boolean(),
    blocked_reasons: z.array(blockedReasonWireSchema),
    observed_policy_version: z.string().nullable().optional(),
    retained_until: isoDateTimeWireSchema.nullable().optional(),
  })
  .strict();

export const deletionPreflightWireSchema = z
  .object({
    scope: scopeWireSchema,
    resource_type: z.enum(["DATASET", "DATASET_VERSION"]),
    resource_id: z.string().min(1),
    capability_status: z.literal("RESERVED_CONDITIONAL"),
    executable: z.literal(false),
    domain_clear: z.boolean(),
    preflight_token: z.string().min(32).max(2048),
    expires_at: isoDateTimeWireSchema,
    checks: z.array(deletionCheckWireSchema).length(7),
    async_impact: z
      .object({
        object_count: decimalIntegerWireSchema,
        estimated_bytes: decimalIntegerWireSchema,
        dependent_projection_count: decimalIntegerWireSchema,
        requires_async_job: z.literal(true),
      })
      .strict(),
    blocked_reasons: z.array(blockedReasonWireSchema),
  })
  .strict();

export type DatasetListEnvelopeWire = z.infer<
  typeof datasetListEnvelopeWireSchema
>;
export type DatasetBootstrapWire = z.infer<
  typeof datasetBootstrapDataWireSchema
>;
export type DatasetSummaryWire = z.infer<typeof datasetSummaryWireSchema>;
export type DatasetFacetsWire = z.infer<typeof datasetFacetsWireSchema>;
export type DatasetsPageCapabilitiesWire = z.infer<
  typeof datasetsPageCapabilitiesWireSchema
>;
export type VersionPageEnvelopeWire = z.infer<
  typeof versionPageEnvelopeWireSchema
>;
export type EpisodePageEnvelopeWire = z.infer<
  typeof episodePageEnvelopeWireSchema
>;
export type VersionBootstrapWire = z.infer<
  typeof versionBootstrapDataWireSchema
>;
export type ReturnReviewResultWire = z.infer<
  typeof returnReviewResultDataWireSchema
>;
export type DeletionPreflightWire = z.infer<typeof deletionPreflightWireSchema>;
export type EpisodeRevisionWire = z.infer<typeof episodeRevisionDataWireSchema>;
export type EpisodeRevisionHistoryWire = z.infer<
  typeof episodeRevisionHistoryWireSchema
>;
export type ReviewChecksWire = z.infer<typeof reviewChecksDataWireSchema>;
export type CreateDatasetRequestWire = z.infer<
  typeof createDatasetRequestWireSchema
>;
export type ApproveReviewCommandWire = z.infer<
  typeof approveReviewCommandWireSchema
>;
export type ApproveReviewResultWire = z.infer<
  typeof approveReviewResultWireSchema
>;
export type ReturnReviewCommandWire = z.infer<
  typeof returnReviewCommandWireSchema
>;
export type VersionManifestWire = z.infer<typeof versionManifestWireSchema>;
export type DatasetVersionSchemaSummaryWire = z.infer<
  typeof datasetVersionSchemaSummaryDataWireSchema
>;
export type SourceProvenancePageWire = z.infer<
  typeof sourceProvenancePageWireSchema
>;
export type DatasetVersionCapacityWire = z.infer<
  typeof datasetVersionCapacityDataWireSchema
>;
export type VersionSchemaWire = z.infer<typeof versionSchemaDataWireSchema>;
export type RequiredStoragePageWire = z.infer<
  typeof requiredStoragePageWireSchema
>;
export type OperationalInventoryPageWire = z.infer<
  typeof operationalInventoryPageWireSchema
>;
