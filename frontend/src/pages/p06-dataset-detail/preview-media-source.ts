import type { Scope } from "../../entities/scope";
import {
  authorizePreview,
  type PreviewPreparationStatus,
} from "../../features/previews/authorize-preview";
import type { ViewerMediaSource } from "../../features/viewer";
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

function resolvePreviewMediaUrl(url: string): string {
  if (/^https?:\/\//u.test(url)) return url;
  const origin = globalThis.location?.origin ?? "http://localhost";
  const apiOrigin = new URL(getRuntimeConfig().apiBaseUrl, origin).origin;
  return new URL(url, apiOrigin).toString();
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
  const authorize = async (
    signal: AbortSignal,
    onStatus?: (status: PreviewPreparationStatus) => void,
  ) => {
    const projectId = input.scope.projectId;
    if (!projectId) throw new Error("当前作用域缺少项目，无法授权预览。");
    const descriptor = await authorizePreview(
      input.scope,
      {
        project_id: projectId,
        dataset_id: input.datasetId,
        rollout_id: input.binding.rollout_id,
        lance_version: String(input.binding.lance_version),
        annotation_revision: input.binding.annotation_revision,
        camera_id: input.binding.camera_id,
        view_mode: "original",
        profile_id: "annotation-h264-720p-v1",
        frequency_hz: input.binding.frequency_hz,
        start_step: input.binding.start_step,
        end_step: input.binding.end_step,
      },
      signal,
      onStatus,
    );
    if (
      descriptor.project_id !== projectId ||
      descriptor.dataset_id !== input.datasetId ||
      descriptor.rollout_id !== input.binding.rollout_id ||
      descriptor.lance_version !== String(input.binding.lance_version) ||
      descriptor.annotation_revision !== input.binding.annotation_revision ||
      descriptor.camera_id !== input.binding.camera_id ||
      descriptor.profile_id !== "annotation-h264-720p-v1"
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
