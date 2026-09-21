import { z } from "zod";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type PublishedExportFormat = components["schemas"]["ExportFormat"];
export type ExportDataStage = "annotated" | "dataset";
export type PublishedExportJob = components["schemas"]["ExportJobV1"];
export type PublishedExportDownload =
  components["schemas"]["ExportDownloadAuthorizationV1"];

const exportEligibilitySchema = z
  .object({
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.string().min(1),
    data_stage: z.enum(["annotated", "dataset"]).default("annotated"),
    eligible_episode_ids: z.array(z.string().min(1)),
  })
  .strict();

const exportFormatSchema = z.enum(["lance_snapshot", "lerobot_v3"]);
const exportResultSchema = z
  .object({
    format: exportFormatSchema,
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.string().min(1),
    manifest_content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    attempt_id: z.string().min(1),
    artifact_content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    row_count: z.number().int().nonnegative(),
    media_type: z.string().min(1),
  })
  .strict();

const exportProgressSchema = z
  .object({
    phase: z.string().min(1),
    completed_phases: z.number().int().nonnegative(),
    total_phases: z.number().int().positive(),
  })
  .strict()
  .refine((value) => value.completed_phases <= value.total_phases, {
    message: "completed_phases cannot exceed total_phases",
  });

const exportJobSchema = z
  .object({
    job_id: z.string().min(1),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.string().min(1),
    format: exportFormatSchema,
    attempt_id: z.string().min(1),
    status: z.enum(["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]),
    stage: z.string().min(1),
    progress: exportProgressSchema,
    cancellation_requested: z.boolean(),
    result: exportResultSchema.nullable().optional(),
    error_code: z.string().nullable().optional(),
    error_message: z.string().nullable().optional(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .strict();

const exportDownloadSchema = z
  .object({
    job_id: z.string().min(1),
    format: exportFormatSchema,
    download_url: z.string().url(),
    expires_at: z.string().datetime({ offset: true }),
    artifact_content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    media_type: z.string().min(1),
  })
  .strict();

export type ExportTarget = Readonly<{
  projectId: string;
  datasetId: string;
  datasetVersion: string;
}>;

function basePath(target: ExportTarget): string {
  return `/datasets/${encodeURIComponent(target.datasetId)}/versions/${encodeURIComponent(
    target.datasetVersion,
  )}/exports`;
}

function parseJob(raw: unknown, endpoint: string): PublishedExportJob {
  return parseWire(exportJobSchema, raw, {
    endpoint,
    schemaVersion: "published-export-job.v1",
  });
}

export async function createPublishedExport(
  target: ExportTarget &
    Readonly<{
      format: PublishedExportFormat;
      idempotencyKey: string;
      episodeIds?: readonly string[];
      dataStage?: ExportDataStage;
    }>,
): Promise<PublishedExportJob> {
  const path = basePath(target);
  const raw = await request<unknown>({
    method: "POST",
    path,
    body: {
      project_id: target.projectId,
      format: target.format,
      ...(target.dataStage ? { data_stage: target.dataStage } : {}),
      ...(target.episodeIds ? { episode_ids: target.episodeIds } : {}),
    },
    idempotencyKey: target.idempotencyKey,
    cache: "no-store",
  });
  return parseJob(raw, path);
}

export async function fetchExportEligibility(
  target: ExportTarget &
    Readonly<{ signal?: AbortSignal; dataStage?: ExportDataStage }>,
) {
  const path = `${basePath(target).replace(/\/exports$/u, "")}/export-eligibility`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    query: {
      project_id: target.projectId,
      data_stage: target.dataStage ?? "annotated",
    },
    signal: target.signal,
    cache: "no-store",
  });
  return parseWire(
    exportEligibilitySchema.refine(
      (value) =>
        value.project_id === target.projectId &&
        value.dataset_id === target.datasetId &&
        value.dataset_version === target.datasetVersion &&
        value.data_stage === (target.dataStage ?? "annotated"),
      {
        message:
          "Export eligibility scope does not match the requested version",
      },
    ),
    raw,
    { endpoint: path, schemaVersion: "export-eligibility.v1" },
  );
}

export async function fetchPublishedExport(
  target: ExportTarget & Readonly<{ jobId: string; signal?: AbortSignal }>,
): Promise<PublishedExportJob> {
  const path = `${basePath(target)}/${encodeURIComponent(target.jobId)}`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    query: { project_id: target.projectId },
    signal: target.signal,
    cache: "no-store",
  });
  return parseJob(raw, path);
}

export async function cancelPublishedExport(
  target: ExportTarget & Readonly<{ jobId: string }>,
): Promise<PublishedExportJob> {
  const path = `${basePath(target)}/${encodeURIComponent(target.jobId)}:cancel`;
  const raw = await request<unknown>({
    method: "POST",
    path,
    query: { project_id: target.projectId },
    cache: "no-store",
  });
  return parseJob(raw, path);
}

export async function retryPublishedExport(
  target: ExportTarget & Readonly<{ jobId: string; idempotencyKey: string }>,
): Promise<PublishedExportJob> {
  const path = `${basePath(target)}/${encodeURIComponent(target.jobId)}:retry`;
  const raw = await request<unknown>({
    method: "POST",
    path,
    query: { project_id: target.projectId },
    idempotencyKey: target.idempotencyKey,
    cache: "no-store",
  });
  return parseJob(raw, path);
}

export async function authorizePublishedExportDownload(
  target: ExportTarget & Readonly<{ jobId: string }>,
): Promise<PublishedExportDownload> {
  const path = `${basePath(target)}/${encodeURIComponent(target.jobId)}/download`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    query: { project_id: target.projectId },
    cache: "no-store",
  });
  return parseWire(exportDownloadSchema, raw, {
    endpoint: path,
    schemaVersion: "published-export-download.v1",
  });
}
