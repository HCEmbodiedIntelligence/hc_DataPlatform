import { z } from "zod";
import type { Scope } from "../../entities/scope";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export interface AlignedMediaSelector {
  readonly project_id: string;
  readonly dataset_id: string;
  readonly rollout_id: string;
  readonly dataset_version: number;
  readonly camera_id: string;
}

const timelineSchema = z
  .object({
    fps: z.literal(30),
    frame_count: z.number().int().positive(),
    first_step: z.number().int().nonnegative(),
    pts_time_base_numerator: z.literal(1),
    pts_time_base_denominator: z.literal(30),
    start_timestamp_ns: z.string().regex(/^(0|[1-9][0-9]*)$/u),
  })
  .strict();

const authorizationSchema = z
  .object({
    schema_version: z.literal("aligned-media-authorization/v1"),
    artifact_id: z.string().min(1),
    artifact_key: z.string().regex(/^[0-9a-f]{64}$/u),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    rollout_id: z.string().min(1),
    dataset_version: z.number().int().positive(),
    camera_id: z.string().min(1),
    media_url: z.string().min(1),
    content_type: z.literal("video/mp4"),
    expires_at: z.iso.datetime({ offset: true }),
    fps: z.literal(30),
    frame_count: z.number().int().positive(),
    duration_seconds: z.number().positive(),
    width: z.number().int().min(2),
    height: z.number().int().min(2),
    timeline: timelineSchema,
    alignment_version: z.string().min(1),
    profile_id: z.string().min(1),
    profile_version: z.string().min(1),
  })
  .strict();

export type AlignedMediaAuthorization = z.infer<typeof authorizationSchema>;
export type MediaAuthorizationStatus = "preparing" | "ready" | "failed";

/** Authorize one ingest-created READY MP4. This operation never creates or polls a job. */
export async function authorizeAlignedMedia(
  scope: Scope,
  selector: AlignedMediaSelector,
  signal: AbortSignal,
  onStatus?: (status: MediaAuthorizationStatus) => void,
): Promise<AlignedMediaAuthorization> {
  try {
    const endpoint = "/aligned-media/authorize";
    const authorization = parseWire(
      authorizationSchema,
      await request<unknown>({
        method: "POST",
        path: endpoint,
        scope,
        cache: "no-store",
        signal,
        body: selector,
      }),
      { endpoint },
    );
    onStatus?.("ready");
    return authorization;
  } catch (error) {
    onStatus?.("failed");
    throw error;
  }
}
