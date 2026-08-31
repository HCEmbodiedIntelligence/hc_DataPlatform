import type { Scope } from "../../entities/scope";
import {
  authorizeAlignedMedia,
  type MediaAuthorizationStatus,
} from "../../features/aligned-media/authorize-aligned-media";
import type { ViewerMediaSource } from "../../features/viewer";
import { getRuntimeConfig } from "../../shared/config/runtime";

export interface EpisodeAlignedMediaBinding {
  readonly rollout_id: string;
  readonly dataset_version: number;
  readonly artifact_id?: string;
  readonly camera_id: string;
  readonly fps: 30;
  readonly start_step: number;
  readonly end_step: number;
}

function resolveAlignedMediaUrl(url: string): string {
  if (/^https?:\/\//u.test(url)) return url;
  const origin = globalThis.location?.origin ?? "http://localhost";
  const apiOrigin = new URL(getRuntimeConfig().apiBaseUrl, origin).origin;
  return new URL(url, apiOrigin).toString();
}

/**
 * Turn an immutable P06 projection binding into a short-lived direct MP4 grant.
 */
export function createDatasetAlignedMediaSource(input: {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly binding: EpisodeAlignedMediaBinding;
  readonly modality: "rgb" | "depth";
}): ViewerMediaSource {
  const authorize = async (
    signal: AbortSignal,
    onStatus?: (status: MediaAuthorizationStatus) => void,
  ) => {
    const projectId = input.scope.projectId;
    if (!projectId) throw new Error("当前作用域缺少项目，无法授权媒体。");
    const descriptor = await authorizeAlignedMedia(
      input.scope,
      {
        project_id: projectId,
        dataset_id: input.datasetId,
        rollout_id: input.binding.rollout_id,
        dataset_version: input.binding.dataset_version,
        camera_id: input.binding.camera_id,
      },
      signal,
      onStatus,
    );
    if (
      descriptor.project_id !== projectId ||
      descriptor.dataset_id !== input.datasetId ||
      descriptor.rollout_id !== input.binding.rollout_id ||
      descriptor.dataset_version !== input.binding.dataset_version ||
      (input.binding.artifact_id !== undefined &&
        descriptor.artifact_id !== input.binding.artifact_id) ||
      descriptor.camera_id !== input.binding.camera_id ||
      descriptor.fps !== input.binding.fps
    ) {
      throw new Error("P06 媒体授权与当前固定采集条目不一致。");
    }
    const kind: "rgb-video" | "depth-preview" =
      input.modality === "depth" ? "depth-preview" : "rgb-video";
    return {
      url: resolveAlignedMediaUrl(descriptor.media_url),
      expiresAt: descriptor.expires_at,
      kind,
    };
  };
  return { authorize, refresh: authorize };
}
