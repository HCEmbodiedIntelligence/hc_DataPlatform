import { z } from "zod";
import type { Scope } from "../../../entities/scope";
import {
  episodeDataReferenceWireSchema,
  episodePackageBindingWireSchema,
  type EpisodePackageBinding,
} from "../api/episode-package-wire";
import type { ViewerMediaSource } from "../../viewer/index";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { datasetDataError } from "./errors";

const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const grantSchema = z
  .object({
    data: episodeDataReferenceWireSchema,
    feature: z.string().min(1),
    profile_id: z.string().min(1),
    kind: z.enum(["rgb-video", "depth-preview"]),
    source_reused: z.boolean(),
    source_video_sha256: digest,
    media_sha256: digest,
    frame_count: z.number().int().positive(),
    fps: z.number().positive(),
    width: z.number().int().positive(),
    height: z.number().int().positive(),
    depth_min_m: z.number().positive().nullable(),
    depth_max_m: z.number().positive().nullable(),
    colorizer: z
      .object({
        implementation: z.literal("pyrealsense2.colorizer"),
        sdk_version: z.string().min(1),
        visual_preset: z.literal("Dynamic"),
        color_scheme: z.literal("Jet"),
        histogram_equalization_enabled: z.literal(true),
        distance_color_scale: z.literal("per_frame_histogram"),
        invalid_depth: z.literal(0),
        display_depth_units_m: z.number().positive(),
        display_input: z.literal("reconstructed_z16_in_memory_only"),
      })
      .strict()
      .nullable(),
    media_url: z.url(),
    expires_at: z.iso.datetime({ offset: true }),
  })
  .strict();

/** Read-only authorization for processing-owned previews; never creates a media job. */
export function createDatasetPackageMediaSource(input: {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly binding: EpisodePackageBinding;
  readonly modality: "rgb" | "depth";
}): ViewerMediaSource {
  const binding = episodePackageBindingWireSchema.parse(input.binding);
  const identity = binding.data.identity;
  if (
    identity.organization_id !== input.scope.organizationId ||
    identity.project_id !== input.scope.projectId ||
    identity.region_code !== input.scope.regionCode ||
    identity.dataset_id !== input.datasetId ||
    binding.value_kind !== null
  ) {
    throw datasetDataError(
      "P06_PACKAGE_BINDING_MISMATCH",
      "视频绑定与当前数据作用域不一致。",
    );
  }
  const expectedKind =
    input.modality === "depth" ? "depth-preview" : "rgb-video";
  const endpoint = `/projects/${encodeURIComponent(identity.project_id)}/regions/${encodeURIComponent(identity.region_code)}/datasets/${encodeURIComponent(identity.dataset_id)}/episodes/${encodeURIComponent(identity.episode_id)}/data-versions/${encodeURIComponent(binding.data.data_version_id)}/media/${encodeURIComponent(binding.feature_key)}/authorize`;
  const authorize = async (signal: AbortSignal) => {
    const raw = await request<unknown>({
      method: "POST",
      path: endpoint,
      scope: input.scope,
      cache: "no-store",
      signal,
    });
    const grant = parseWire(grantSchema, raw, { endpoint });
    const url = new URL(grant.media_url);
    if (
      JSON.stringify(grant.data) !== JSON.stringify(binding.data) ||
      grant.feature !== binding.feature_key ||
      grant.kind !== expectedKind ||
      grant.frame_count !== binding.data.frame_count ||
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      Date.parse(grant.expires_at) <= Date.now() ||
      (grant.kind === "depth-preview" &&
        (grant.colorizer === null ||
          grant.source_reused ||
          grant.depth_min_m === null ||
          grant.depth_max_m === null ||
          grant.depth_max_m <= grant.depth_min_m))
    ) {
      throw datasetDataError(
        "P06_MEDIA_IDENTITY_MISMATCH",
        "预览授权与固定的 Episode 视频不一致。",
      );
    }
    return {
      url: grant.media_url,
      expiresAt: grant.expires_at,
      kind: grant.kind,
      mediaStartSeconds: binding.start_step / grant.fps,
      mediaEndSeconds: binding.end_step / grant.fps,
    };
  };
  return { authorize, refresh: authorize };
}
