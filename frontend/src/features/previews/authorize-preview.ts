import { z } from "zod";
import type { Scope } from "../../entities/scope";
import { request } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";
import { parseWire } from "../../shared/api/validate";

export type PreviewDescriptor = components["schemas"]["PreviewDescriptorV1"];
export type PreviewRequest = components["schemas"]["PreviewRequestV1"];
export type PreviewPreparationStatus = "preparing" | "ready" | "failed";

const pendingSchema = z
  .object({
    schema_version: z.number().int(),
    status: z.enum(["QUEUED", "RUNNING", "FAILED"]),
    artifact_key: z.string().regex(/^[0-9a-f]{64}$/u),
    job_id: z.string().min(1),
    status_url: z.string().min(1),
    retry_after_seconds: z.number().int().min(1).max(60),
    error_code: z.string().nullable().optional(),
  })
  .strict();

const jobSchema = z
  .object({
    schema_version: z.number().int(),
    status: z.enum(["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]),
    artifact_key: z.string().regex(/^[0-9a-f]{64}$/u),
    job_id: z.string().min(1),
    progress: z.number().int().min(0).max(100),
    retry_after_seconds: z.number().int().min(1).max(60).nullable().optional(),
    error_code: z.string().nullable().optional(),
  })
  .passthrough();

const descriptorSchema = z
  .object({
    schema_version: z.number().int(),
    session_id: z.string().min(1),
    artifact_key: z.string().regex(/^[0-9a-f]{64}$/u),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    rollout_id: z.string().min(1),
    lance_version: z.string().min(1),
    annotation_revision: z.number().int().nonnegative(),
    camera_id: z.string().min(1),
    profile_id: z.string().min(1),
    playlist_url: z.string().min(1),
    signed_url_expires_at: z.string().datetime({ offset: true }),
  })
  .passthrough();

function aborted(): DOMException {
  return new DOMException("Preview authorization was aborted", "AbortError");
}

function delay(seconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(resolve, seconds * 1_000);
    signal.addEventListener(
      "abort",
      () => {
        globalThis.clearTimeout(timeout);
        reject(aborted());
      },
      { once: true },
    );
  });
}

function previewFailure(code: string | null | undefined): Error {
  return new Error(`预览生成失败${code ? `（${code}）` : ""}，请重试此面板。`);
}

/**
 * Authorize a READY artifact or poll one durable media job. Polling is bounded,
 * sequential, and tied to the viewer's AbortSignal so unmounts stop every request.
 */
export async function authorizePreview(
  scope: Scope,
  body: PreviewRequest,
  signal: AbortSignal,
  onStatus?: (status: PreviewPreparationStatus) => void,
): Promise<PreviewDescriptor> {
  const endpoint = "/previews/sessions";
  let completedJobId: string | null = null;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (signal.aborted) throw aborted();
    const raw = await request<unknown>({
      method: "POST",
      path: endpoint,
      scope,
      cache: "no-store",
      signal,
      body,
    });
    const descriptor = descriptorSchema.safeParse(raw);
    if (descriptor.success) {
      onStatus?.("ready");
      return descriptor.data as PreviewDescriptor;
    }
    const pending = parseWire(pendingSchema, raw, { endpoint });
    if (pending.status === "FAILED") {
      onStatus?.("failed");
      throw previewFailure(pending.error_code);
    }
    onStatus?.("preparing");
    if (completedJobId === pending.job_id)
      throw new Error("预览任务已完成，但媒体制品尚不可授权。");
    await delay(pending.retry_after_seconds, signal);
    const job = parseWire(
      jobSchema,
      await request<unknown>({
        method: "GET",
        path: pending.status_url,
        scope,
        cache: "no-store",
        signal,
      }),
      { endpoint: pending.status_url },
    );
    if (job.job_id !== pending.job_id || job.artifact_key !== pending.artifact_key) {
      onStatus?.("failed");
      throw new Error("预览任务状态与当前媒体请求不一致。");
    }
    if (job.status === "FAILED" || job.status === "CANCELLED") {
      onStatus?.("failed");
      throw previewFailure(job.error_code);
    }
    completedJobId = job.status === "SUCCEEDED" ? job.job_id : null;
  }
  onStatus?.("failed");
  throw new Error("预览准备超时，请稍后重试此面板。");
}
