import { z } from "zod";
import type { Scope } from "../../entities/scope";
import type { ViewerMediaSource } from "../../features/viewer";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import { getRuntimeConfig } from "../../shared/config/runtime";

export interface EpisodePreviewBinding {
  readonly rollout_id: string;
  readonly lance_version: number;
  readonly annotation_revision: number;
  readonly camera_id: string;
  readonly frequency_hz: number;
  readonly start_step: number;
  readonly end_step: number;
}

const encodingProfileWireSchema = z
  .object({
    name: z.string().min(1),
    width: z.number().int().min(16),
    height: z.number().int().min(16),
    video_codec: z.enum(["h264", "vp9"]),
    pixel_format: z.literal("yuv420p"),
    video_bitrate_kbps: z.number().int().positive(),
    segment_duration_seconds: z.number().positive(),
    preset: z.string().min(1),
  })
  .strict();

const previewDescriptorWireSchema = z
  .object({
    schema_version: z.literal(1),
    session_id: z.string().min(1),
    cache_key: z.string().min(1),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    rollout_id: z.string().min(1),
    lance_version: z.string().min(1),
    annotation_revision: z.number().int().nonnegative(),
    camera_id: z.string().min(1),
    view_mode: z.enum(["original", "edited", "compare"]),
    encoding_profile: encodingProfileWireSchema,
    playlist_url: z.string().min(1),
    media_type: z.string().min(1),
    frame_count: z.number().int().nonnegative(),
    placeholder_count: z.number().int().nonnegative(),
    placeholders: z.array(
      z
        .object({
          code: z.literal("INVALID_IMAGE_STEP"),
          invalid_reason: z.string().min(1),
          playback_frame: z.number().int().nonnegative(),
          step_index: z.number().int().nonnegative(),
        })
        .strict(),
    ),
    duration_seconds: z.number().nonnegative(),
    timeline: z
      .object({
        frequency_hz: z.number().positive(),
        segments: z.array(
          z
            .object({
              playback_start_seconds: z.number().nonnegative(),
              playback_end_seconds: z.number().positive(),
              source_start_step: z.number().int().nonnegative(),
              source_end_step: z.number().int().positive(),
              excluded: z.boolean(),
            })
            .strict(),
        ),
      })
      .strict(),
    cache_expires_at: z.string().datetime({ offset: true }),
    signed_url_expires_at: z.string().datetime({ offset: true }),
  })
  .strict();

function resolvePreviewMediaUrl(url: string): string {
  if (/^https?:\/\//u.test(url)) return url;
  const origin = globalThis.location?.origin ?? "http://localhost";
  const apiOrigin = new URL(getRuntimeConfig().apiBaseUrl, origin).origin;
  return new URL(url, apiOrigin).toString();
}

function preferredPreviewEncodingProfile():
  | {
      readonly name: "vp9-cmaf-preview-v1";
      readonly video_codec: "vp9";
    }
  | undefined {
  const mediaSource = globalThis.MediaSource;
  if (
    typeof mediaSource?.isTypeSupported === "function" &&
    !mediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"') &&
    mediaSource.isTypeSupported('video/mp4; codecs="vp09.00.10.08"')
  ) {
    return {
      name: "vp9-cmaf-preview-v1",
      video_codec: "vp9",
    };
  }
  return undefined;
}

/**
 * Turn an immutable, scoped P06 projection binding into a lazily-issued HLS
 * capability.  The client never receives a physical Lance/object locator and
 * reuses the same descriptor endpoint as annotation workbenches, so expiry and
 * per-session media-grant auditing stay centralized in the preview service.
 */
export function createDatasetPreviewMediaSource(input: {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly binding: EpisodePreviewBinding;
  readonly modality: "rgb" | "depth";
}): ViewerMediaSource {
  const authorize = async (signal: AbortSignal) => {
    const endpoint = "/previews/sessions";
    const encodingProfile = preferredPreviewEncodingProfile();
    const raw = await request<unknown>({
      method: "POST",
      path: endpoint,
      scope: input.scope,
      cache: "no-store",
      signal,
      body: {
        project_id: input.scope.projectId,
        dataset_id: input.datasetId,
        rollout_id: input.binding.rollout_id,
        lance_version: String(input.binding.lance_version),
        annotation_revision: input.binding.annotation_revision,
        camera_id: input.binding.camera_id,
        view_mode: "original",
        frequency_hz: input.binding.frequency_hz,
        ...(encodingProfile === undefined
          ? {}
          : { encoding_profile: encodingProfile }),
        start_step: input.binding.start_step,
        end_step: input.binding.end_step,
      },
    });
    const descriptor = parseWire(previewDescriptorWireSchema, raw, {
      endpoint,
    });
    if (
      descriptor.project_id !== input.scope.projectId ||
      descriptor.dataset_id !== input.datasetId ||
      descriptor.rollout_id !== input.binding.rollout_id ||
      descriptor.lance_version !== String(input.binding.lance_version) ||
      descriptor.annotation_revision !== input.binding.annotation_revision ||
      descriptor.camera_id !== input.binding.camera_id ||
      descriptor.encoding_profile.video_codec !==
        (encodingProfile?.video_codec ?? "h264")
    ) {
      throw new Error("P06 预览授权与当前固定采集条目不一致。");
    }
    const kind: "rgb-video" | "depth-preview" =
      input.modality === "depth" ? "depth-preview" : "rgb-video";
    return {
      url: resolvePreviewMediaUrl(descriptor.playlist_url),
      expiresAt: descriptor.signed_url_expires_at,
      kind,
    };
  };
  return { authorize, refresh: authorize };
}
