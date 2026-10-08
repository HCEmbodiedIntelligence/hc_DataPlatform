import { z } from "zod";
import type { Scope } from "../../shared/scope/model";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";

const pixelWire = z.object({
  data: episodeDataReferenceWireSchema,
  feature: z.string(),
  frame_index: z.number().int().nonnegative(),
  x: z.number().int().nonnegative(),
  y: z.number().int().nonnegative(),
  shape: z.tuple([z.number().int().positive(), z.number().int().positive()]),
  value: z.number().finite().positive().nullable(),
  unit: z.literal("m"),
  valid: z.boolean(),
  saturated_low: z.boolean(),
  saturated_high: z.boolean(),
  flags: z.number().int().min(0).max(7),
  quantized: z.literal(true),
  coordinate_space: z.literal("depth_native"),
  profile: z
    .object({
      bits: z.literal(12),
      depth_min: z.number().positive(),
      depth_max: z.number().positive(),
      use_log: z.boolean(),
      profile_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    })
    .passthrough(),
  source: z.record(z.string(), z.unknown()),
});

export type DepthFrameTarget = {
  scope: Scope;
  data: ReviewDataRef;
  feature: string;
  frame: number;
};

function framePath(target: DepthFrameTarget) {
  const { scope, data, frame } = target;
  const id = data.identity;
  if (
    id.organization_id !== scope.organizationId ||
    id.project_id !== scope.projectId ||
    id.region_code !== scope.regionCode ||
    !Number.isSafeInteger(frame) ||
    frame < 0 ||
    frame >= data.frame_count
  )
    throw new Error("深度帧超出当前数据版本或作用域。");
  return `/projects/${encodeURIComponent(id.project_id)}/regions/${encodeURIComponent(id.region_code)}/datasets/${encodeURIComponent(id.dataset_id)}/episodes/${encodeURIComponent(id.episode_id)}/data-versions/${encodeURIComponent(data.data_version_id)}/depth/${encodeURIComponent(target.feature)}/${frame}`;
}

export async function loadDepthPreview(
  target: DepthFrameTarget,
  signal?: AbortSignal,
) {
  return request<Blob>({
    method: "GET",
    path: framePath(target),
    scope: target.scope,
    query: { representation: "png", unit: "m" },
    responseType: "blob",
    cache: "no-store",
    signal,
  });
}

export async function loadDepthPixel(
  target: DepthFrameTarget,
  x: number,
  y: number,
  signal?: AbortSignal,
) {
  if (![x, y].every((value) => Number.isSafeInteger(value) && value >= 0))
    throw new Error("像素坐标无效。");
  const result = pixelWire.parse(
    await request<unknown>({
      method: "GET",
      path: `${framePath(target)}/pixel`,
      scope: target.scope,
      query: { x, y, unit: "m" },
      cache: "no-store",
      signal,
    }),
  );
  if (
    JSON.stringify(result.data) !==
      JSON.stringify(episodeDataReferenceWireSchema.parse(target.data)) ||
    result.feature !== target.feature ||
    result.frame_index !== target.frame ||
    result.x !== x ||
    result.y !== y ||
    x >= result.shape[1] ||
    y >= result.shape[0] ||
    result.valid !== (result.value !== null) ||
    result.valid !== Boolean(result.flags & 1) ||
    result.saturated_low !== Boolean(result.flags & 2) ||
    result.saturated_high !== Boolean(result.flags & 4)
  )
    throw new Error("像素结果与固定帧或有效掩码不一致。");
  return result;
}

export function imagePixel(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
) {
  const x = (clientX - rect.left) / rect.width;
  const y = (clientY - rect.top) / rect.height;
  return width > 0 && height > 0 && x >= 0 && x < 1 && y >= 0 && y < 1
    ? { x: Math.floor(x * width), y: Math.floor(y * height) }
    : null;
}
