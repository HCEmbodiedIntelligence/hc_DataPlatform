import { z } from "zod";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import type { IngestScope } from "../../../entities/data-source";
import { nativeRoot } from "../processing/lerobot-processing";

type Schemas = components["schemas"];
export type OriginalFile = Schemas["OriginalFile"];
export type OriginalEpisode = Schemas["OriginalEpisode"];
const count = z.number().int().nonnegative();
const path = z.string().min(1);
const fileWire = z.object({
  path,
  size: count,
  sha256: z.string().regex(/^[a-f0-9]{64}$/u),
}) satisfies z.ZodType<OriginalFile>;
const filePageWire = z.object({
  import_id: path,
  source_format: path,
  episode_count: count,
  files: z.array(fileWire),
  total: count,
  offset: count,
}) satisfies z.ZodType<Schemas["OriginalFilePage"]>;
export const originalEpisodeWire = z.object({
  episode_index: count,
  frame_count: count,
  fps: z.number().positive(),
  duration_ns: z.string().regex(/^[1-9][0-9]*$/u),
  videos: z.array(
    z
      .object({
        camera_id: path,
        path,
        start_seconds: z.number().nonnegative(),
        end_seconds: z.number().nonnegative(),
        media_kind: z.enum(["RGB", "DEPTH", "UNKNOWN"]),
        preview_kind: z.enum(["ORIGINAL_VIDEO", "DOWNLOAD_ONLY"]),
        preview_reason: z
          .enum(["RAW_DEPTH_REQUIRES_COLORIZATION", "MEDIA_KIND_UNDECLARED"])
          .nullish(),
      })
      .superRefine((video, ctx) => {
        if (
          video.end_seconds <= video.start_seconds ||
          (video.preview_kind === "ORIGINAL_VIDEO" &&
            video.media_kind !== "RGB")
        )
          ctx.addIssue({ code: "custom", message: "原始媒体预览声明不一致" });
      }),
  ),
}) satisfies z.ZodType<OriginalEpisode>;
const grantWire = z.object({
  path,
  url: z.string().min(1),
  expires_at: z.iso.datetime({ offset: true }),
}) satisfies z.ZodType<Schemas["OriginalFileGrant"]>;
function base(scope: IngestScope, importId: string) {
  return `${nativeRoot(scope)}/${encodeURIComponent(importId)}`;
}
export async function getOriginalFiles(
  scope: IngestScope,
  importId: string,
  offset: number,
  signal: AbortSignal,
) {
  const endpoint = `${base(scope, importId)}/files`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    scopeMode: "explicit",
    signal,
    cache: "no-store",
    query: { offset, limit: 50 },
  });
  return parseWire(
    filePageWire.refine(
      (value) => value.import_id === importId && value.offset === offset,
    ),
    raw,
    { endpoint },
  );
}
export async function getOriginalEpisode(
  scope: IngestScope,
  importId: string,
  index: number,
  signal: AbortSignal,
) {
  const endpoint = `${base(scope, importId)}/episodes/${index}`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    scopeMode: "explicit",
    signal,
    cache: "no-store",
  });
  return parseWire(
    originalEpisodeWire.refine((value) => value.episode_index === index),
    raw,
    { endpoint },
  );
}
export async function authorizeOriginalFile(
  scope: IngestScope,
  importId: string,
  filePath: string,
  download: boolean,
  signal?: AbortSignal,
) {
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    const endpoint = `${base(scope, importId)}/assets:read`;
    const raw = await request<unknown>({
      method: "GET",
      path: endpoint,
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
      query: { path: filePath, ...(download ? { download: true } : {}) },
    });
    lease.signal.throwIfAborted();
    return parseWire(
      grantWire.refine(
        (value) =>
          value.path === filePath && Date.parse(value.expires_at) > Date.now(),
      ),
      raw,
      { endpoint },
    );
  } finally {
    lease.release();
  }
}
export function originalPreviewReason(
  video: OriginalEpisode["videos"][number],
) {
  return video.media_kind === "DEPTH"
    ? "原始深度视频需转换为彩色预览后才能播放。原始深度字节完整保留，可在下方下载原文件。"
    : "源文件未声明可播放的媒体类型，可在下方下载原文件。";
}
