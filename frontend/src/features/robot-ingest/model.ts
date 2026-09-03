import { z } from "zod";

const timestamp = z.string().min(1);

export const robotUploadPolicySchema = z.object({
  code: z.string().min(1),
  max_asset_size_bytes: z.number().int().positive(),
  max_batch_size_bytes: z.number().int().positive(),
  max_assets: z.number().int().positive(),
  part_authorization_ttl_seconds: z.number().int().positive(),
  session_retention_hours: z.number().int().positive(),
  require_sha256: z.boolean(),
  require_crc64: z.boolean(),
});

export const robotIdentitySchema = z.object({
  ingest_identity_id: z.string().min(1),
  organization_id: z.string().min(1),
  robot_id: z.string().min(1),
  display_name: z.string().nullable(),
  state: z.enum(["ENABLED", "DISABLED"]),
  allowed_transports: z.array(z.string().min(1)),
  allowed_formats: z.array(z.string().min(1)),
  upload_policy: robotUploadPolicySchema,
  credential_revision: z.number().int().nonnegative(),
  last_authenticated_at: timestamp.nullable(),
  last_seen_at: timestamp.nullable(),
  last_upload_at: timestamp.nullable(),
  created_at: timestamp,
  updated_at: timestamp,
});

export const issuedCredentialSchema = z.object({
  credential_id: z.string().min(1),
  credential_version: z.number().int().positive(),
  token: z.string().min(32),
  token_prefix: z.string().min(8),
  issued_at: timestamp,
  expires_at: timestamp.nullable(),
});

export const credentialSummarySchema = z.object({
  credential_id: z.string().min(1),
  credential_version: z.number().int().positive(),
  state: z.enum(["ACTIVE", "REVOKED", "EXPIRED"]),
  token_prefix: z.string().min(8),
  issued_at: timestamp,
  expires_at: timestamp.nullable(),
  revoked_at: timestamp.nullable(),
  last_authenticated_at: timestamp.nullable(),
});

export const robotIdentityEnvelopeSchema = z.object({
  data: robotIdentitySchema,
  credential: issuedCredentialSchema.nullable(),
});

export const robotIdentityListSchema = z.object({
  items: z.array(robotIdentitySchema),
});

export const robotUploadSummarySchema = z.object({
  upload_id: z.string().min(1),
  client_upload_id: z.string().min(1),
  authenticated_robot_id: z.string().min(1),
  request_robot_id: z.string().min(1),
  collection_job_id: z.string().min(1),
  capture_mode: z.enum(["PRESEGMENTED", "CONTINUOUS"]),
  source_format: z.string().min(1),
  source_format_version: z.string().min(1),
  declared_episode_count: z.number().int().nonnegative().nullable(),
  verified_episode_count: z.number().int().nonnegative().nullable(),
  derived_episode_count: z.number().int().nonnegative(),
  verified_frame_count: z.number().int().nonnegative(),
  verified_sample_count: z.number().int().nonnegative(),
  qc_pass_episode_count: z.number().int().nonnegative(),
  qc_risk_episode_count: z.number().int().nonnegative(),
  qc_reject_episode_count: z.number().int().nonnegative(),
  total_bytes: z.number().int().positive(),
  state: z.enum([
    "UPLOADING",
    "PAUSED",
    "READY_TO_COMMIT",
    "COMMITTED",
    "FAILED",
    "CANCELLED",
  ]),
  processing_status: z.enum([
    "PENDING",
    "DISCOVERING_EPISODES",
    "PROCESSING",
    "READY",
    "PARTIALLY_FAILED",
    "FAILED",
  ]),
  quality_status: z.enum(["PENDING", "PASS", "RISK", "REJECT"]),
  target: z.object({
    collection_task_id: z.string().min(1),
    organization_id: z.string().min(1),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    region_code: z.string().min(1),
  }),
  raw_source_id: z.string().nullable(),
  created_at: timestamp,
  expires_at: timestamp,
  updated_at: timestamp,
  committed_at: timestamp.nullable(),
});

export const robotUploadListSchema = z.object({
  items: z.array(robotUploadSummarySchema),
});

export const robotAttemptSchema = z.object({
  attempt_id: z.string().min(1),
  organization_id: z.string().nullable(),
  project_id: z.string().nullable(),
  region_code: z.string().nullable(),
  authenticated_robot_id: z.string().nullable(),
  request_robot_id: z.string().nullable(),
  collection_task_id: z.string().nullable(),
  source_format: z.string().nullable(),
  outcome: z.enum(["ACCEPTED", "REJECTED", "COMMITTED", "FAILED"]),
  failure_stage: z.string().min(1),
  failure_code: z.string().nullable(),
  upload_id: z.string().nullable(),
  raw_source_id: z.string().nullable(),
  occurred_at: timestamp,
});

export const robotAttemptListSchema = z.object({
  items: z.array(robotAttemptSchema),
});

export const robotEpisodeResultSchema = z.object({
  episode_id: z.string().min(1),
  source_episode_index: z.number().int().nonnegative(),
  status: z.enum(["PENDING", "PROCESSING", "READY", "FAILED"]),
  frame_count: z.number().int().positive().nullable(),
  sample_count: z.number().int().nonnegative().nullable(),
  dataset_version: z.number().int().positive().nullable(),
  lance_version: z.number().int().positive().nullable(),
  quality_status: z.enum(["PENDING", "PASS", "RISK", "REJECT"]),
  qc_report_id: z.string().nullable(),
  created_at: timestamp,
  updated_at: timestamp,
});

export const robotEpisodeResultListSchema = z.object({
  upload_id: z.string().min(1),
  raw_source_id: z.string().min(1),
  items: z.array(robotEpisodeResultSchema),
});

export const robotStatisticsSchema = z.object({
  robot_id: z.string().min(1),
  upload_batch_count: z.number().int().nonnegative(),
  committed_raw_count: z.number().int().nonnegative(),
  episode_count: z.number().int().nonnegative(),
  frame_count: z.number().int().nonnegative(),
  sample_count: z.number().int().nonnegative(),
  capture_duration_ns: z.number().int().nonnegative(),
  raw_bytes: z.number().int().nonnegative(),
  qc_pass_count: z.number().int().nonnegative(),
  qc_risk_count: z.number().int().nonnegative(),
  qc_reject_count: z.number().int().nonnegative(),
  technical_failure_count: z.number().int().nonnegative(),
  qualified_rate: z.number().min(0).max(1).nullable(),
  evaluated_episode_count: z.number().int().nonnegative(),
});

export type RobotUploadPolicy = z.infer<typeof robotUploadPolicySchema>;
export type RobotIdentity = z.infer<typeof robotIdentitySchema>;
export type IssuedRobotCredential = z.infer<typeof issuedCredentialSchema>;
export type RobotCredentialSummary = z.infer<typeof credentialSummarySchema>;
export type RobotUploadSummary = z.infer<typeof robotUploadSummarySchema>;
export type RobotAttempt = z.infer<typeof robotAttemptSchema>;
export type RobotEpisodeResult = z.infer<typeof robotEpisodeResultSchema>;
export type RobotStatistics = z.infer<typeof robotStatisticsSchema>;
