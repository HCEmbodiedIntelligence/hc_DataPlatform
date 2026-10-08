import { z } from "zod";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { getRuntimeConfig } from "../../../shared/config/runtime";
import type { Scope } from "../../../shared/scope/model";
import { datasetDataError } from "../episodes/errors";

type Schemas = components["schemas"];
export type LogicalSnapshot = Schemas["LogicalSnapshotView"];
export type LogicalStream = Schemas["LogicalStream"];
export type LogicalSample = Schemas["LogicalSample"];
export interface LogicalSelection {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly versionId: string;
  readonly revisionId: string;
  readonly episodeId: string;
  readonly contentSha256: string;
  readonly snapshotToken: string;
}
const id = z.string().min(1);
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const snapshotId = z.string().regex(/^snapshot_[a-f0-9]{64}$/u);
const index = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const ns = z.string().regex(/^(0|[1-9][0-9]*)$/u);
const streamWire = z
  .object({
    stream_id: id,
    name: id,
    kind: id,
    value_kind: id.nullish(),
    sample_count: index,
    temporal: z.boolean(),
    start_ns: ns.nullish(),
    end_ns: ns.nullish(),
  })
  .superRefine((stream, context) => {
    if (
      stream.temporal
        ? !stream.start_ns ||
          !stream.end_ns ||
          BigInt(stream.end_ns) <= BigInt(stream.start_ns)
        : stream.start_ns != null || stream.end_ns != null
    )
      context.addIssue({
        code: "custom",
        message: "Logical stream time domain is inconsistent",
      });
  }) satisfies z.ZodType<LogicalStream>;
const snapshotWire = z.object({
  snapshot_id: snapshotId,
  snapshot: z.object({
    format: z.literal("hc-logical-snapshot/1"),
    selection: z.object({
      organization_id: id,
      project_id: id,
      region_code: id,
      dataset_id: id,
      version_id: id,
      revision_id: id,
    }),
    episode_id: id,
    content_sha256: digest,
    streams: z.array(streamWire),
  }),
}) satisfies z.ZodType<LogicalSnapshot>;
const sampleWire = z.object({
  sample_id: id,
  sample_index: index,
  timestamp_ns: ns.nullish(),
  value: z.unknown(),
  valid: z.boolean(),
  repeated: z.boolean(),
  source_timestamps_ns: z.array(ns),
  time_error_ns: z
    .string()
    .regex(/^-?[0-9]+$/u)
    .nullish(),
}) satisfies z.ZodType<LogicalSample>;
const batchWire = z.object({
  snapshot_id: snapshotId,
  stream_id: id,
  start: index,
  end: index,
  samples: z.array(sampleWire),
}) satisfies z.ZodType<Schemas["LogicalSampleBatch"]>;
const mediaWire = z.object({
  snapshot_id: snapshotId,
  stream_id: id,
  kind: z.enum(["video", "depth-preview"]),
  media_url: z.string().min(1),
  content_type: z.literal("video/mp4"),
  expires_at: z.iso.datetime({ offset: true }),
  sample_count: index.positive(),
  sample_rate_hz: z.number().positive(),
  media_start_seconds: z.number().nonnegative(),
  width: index.positive(),
  height: index.positive(),
  media_sha256: digest.nullish(),
  depth_min_m: z.number().nullish(),
  depth_max_m: z.number().nullish(),
  colorizer: z.record(z.string(), z.unknown()).nullish(),
}) satisfies z.ZodType<Schemas["LogicalMediaGrant"]>;

function mismatch(message: string): never {
  throw datasetDataError("LOGICAL_IDENTITY_MISMATCH", message);
}
function endpoint(selection: LogicalSelection) {
  const { scope, datasetId, versionId, revisionId } = selection;
  if (
    !scope.organizationId ||
    !scope.projectId ||
    !scope.regionCode ||
    !selection.snapshotToken
  )
    mismatch("读取需要明确的组织、项目、区域及固定快照。");
  const part = encodeURIComponent;
  return `/projects/${part(scope.projectId)}/regions/${part(scope.regionCode)}/datasets/${part(datasetId)}/versions/${part(versionId)}/episode-revisions/${part(revisionId)}/logical`;
}
export async function fetchLogicalSnapshot(
  selection: LogicalSelection,
  signal: AbortSignal,
) {
  const path = endpoint(selection);
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope: selection.scope,
    scopeMode: "explicit",
    signal,
    cache: "no-store",
    query: { snapshot_token: selection.snapshotToken },
  });
  const view = parseWire(snapshotWire, raw, { endpoint: path });
  const actual = view.snapshot.selection;
  if (
    actual.organization_id !== selection.scope.organizationId ||
    actual.project_id !== selection.scope.projectId ||
    actual.region_code !== selection.scope.regionCode ||
    actual.dataset_id !== selection.datasetId ||
    actual.version_id !== selection.versionId ||
    actual.revision_id !== selection.revisionId ||
    view.snapshot.episode_id !== selection.episodeId ||
    view.snapshot.content_sha256 !== selection.contentSha256.toLowerCase() ||
    new Set(view.snapshot.streams.map((s) => s.stream_id)).size !==
      view.snapshot.streams.length
  )
    mismatch("逻辑快照与当前固定版本、Episode 或作用域不一致。");
  return view;
}
export async function fetchLogicalSamples(
  selection: LogicalSelection,
  view: LogicalSnapshot,
  stream: LogicalStream,
  start: number,
  end: number,
  signal: AbortSignal,
) {
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    start < 0 ||
    end <= start ||
    end > stream.sample_count ||
    end - start > 4096
  )
    mismatch("样本窗口超出当前固定数据范围。");
  const path = `${endpoint(selection)}/streams/${encodeURIComponent(stream.stream_id)}/samples`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope: selection.scope,
    scopeMode: "explicit",
    signal,
    cache: "no-store",
    query: {
      snapshot_token: selection.snapshotToken,
      snapshot_id: view.snapshot_id,
      start,
      end,
    },
  });
  const batch = parseWire(batchWire, raw, { endpoint: path });
  if (
    batch.snapshot_id !== view.snapshot_id ||
    batch.stream_id !== stream.stream_id ||
    batch.start !== start ||
    batch.end !== end ||
    batch.samples.length !== end - start ||
    new Set(batch.samples.map((s) => s.sample_id)).size !==
      batch.samples.length ||
    batch.samples.some(
      (s, i) =>
        s.sample_index !== start + i ||
        (stream.temporal
          ? s.timestamp_ns == null ||
            BigInt(s.timestamp_ns) < BigInt(stream.start_ns!) ||
            BigInt(s.timestamp_ns) >= BigInt(stream.end_ns!)
          : s.timestamp_ns != null),
    )
  )
    mismatch("样本响应未完整覆盖所选快照和窗口。");
  return batch.samples;
}
export async function authorizeLogicalMedia(
  selection: LogicalSelection,
  view: LogicalSnapshot,
  stream: LogicalStream,
  signal: AbortSignal,
) {
  const path = `${endpoint(selection)}/streams/${encodeURIComponent(stream.stream_id)}/media/authorize`;
  const raw = await request<unknown>({
    method: "POST",
    path,
    scope: selection.scope,
    scopeMode: "explicit",
    signal,
    cache: "no-store",
    query: {
      snapshot_token: selection.snapshotToken,
      snapshot_id: view.snapshot_id,
    },
  });
  const grant = parseWire(mediaWire, raw, { endpoint: path });
  const apiOrigin = new URL(
    getRuntimeConfig().apiBaseUrl,
    globalThis.location?.origin ?? "http://localhost",
  ).origin;
  const url = new URL(grant.media_url, apiOrigin);
  if (
    grant.snapshot_id !== view.snapshot_id ||
    grant.stream_id !== stream.stream_id ||
    grant.sample_count !== stream.sample_count ||
    (stream.kind.toUpperCase() === "DEPTH") !==
      (grant.kind === "depth-preview") ||
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    Date.parse(grant.expires_at) <= Date.now()
  )
    mismatch("媒体授权与当前固定快照不一致或已失效。");
  return { ...grant, media_url: url.toString() };
}
