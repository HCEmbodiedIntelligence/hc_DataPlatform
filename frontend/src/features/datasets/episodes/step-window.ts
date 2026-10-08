import { z } from "zod";
import type {
  ViewerEventSample,
  ViewerPointFrame,
  ViewerSeriesDescriptor,
  ViewerWindow,
  ViewerWindowPayload,
} from "../../viewer/index";
import { datasetDataError } from "./errors";
export { datasetDataError } from "./errors";

export interface StepValueBinding {
  readonly rollout_id: string;
  readonly modality_key: string;
  readonly value_kind: "SCALAR" | "VECTOR" | "POINTCLOUD_XYZ" | "EVENT";
  readonly start_step: number;
  readonly end_step: number;
}

const decimalNsWireSchema = z.string().regex(/^(0|[1-9][0-9]*)$/u);
export const safeStepWireSchema = z
  .number()
  .int()
  .min(0)
  .max(Number.MAX_SAFE_INTEGER);

export const stepRecordWireSchema = z
  .object({
    schema_version: z.string().min(1),
    rollout_id: z.string().min(1),
    step_index: safeStepWireSchema,
    timestamp_ns: decimalNsWireSchema,
    modalities: z.record(z.string(), z.unknown()),
    source_timestamps_ns: z.record(z.string(), z.array(z.unknown())),
    time_error_ns: z.record(z.string(), z.number().int().nullable()),
    valid: z.record(z.string(), z.boolean()),
    repeated: z.record(z.string(), z.boolean()),
    sample_valid: z.boolean(),
  })
  .strict();

function clamp(value: bigint, low: bigint, high: bigint): bigint {
  return value < low ? low : value > high ? high : value;
}

/**
 * Maps a viewer time interval into the explicit immutable step interval.  The
 * arithmetic stays in bigint until after it is bounded by the browser-safe
 * binding values, so nanosecond timeline precision is never rounded.
 */
export function stepWindowForTime(
  window: ViewerWindow,
  input: {
    readonly streamStartNs: string;
    readonly streamEndNs: string;
    readonly binding: Pick<StepValueBinding, "start_step" | "end_step">;
  },
): { readonly startStep: number; readonly endStep: number } {
  const streamStart = BigInt(input.streamStartNs);
  const streamEnd = BigInt(input.streamEndNs);
  const duration = streamEnd - streamStart;
  if (duration <= 0n)
    throw datasetDataError("P06_STREAM_RANGE_INVALID", "Stream 时间范围无效。");
  const bindingSpan = BigInt(input.binding.end_step - input.binding.start_step);
  if (bindingSpan <= 0n)
    throw datasetDataError("P06_STEP_RANGE_INVALID", "固定数据步骤范围无效。");

  const requestedStart = clamp(BigInt(window.startNs), streamStart, streamEnd);
  const requestedEnd = clamp(
    BigInt(window.endNs),
    requestedStart + 1n,
    streamEnd,
  );
  const relativeStart = requestedStart - streamStart;
  const relativeEnd = requestedEnd - streamStart;
  const offsetStart = (relativeStart * bindingSpan) / duration;
  const offsetEnd = (relativeEnd * bindingSpan + duration - 1n) / duration;
  const start = BigInt(input.binding.start_step) + offsetStart;
  const unclampedEnd = BigInt(input.binding.start_step) + offsetEnd;
  const end = unclampedEnd <= start ? start + 1n : unclampedEnd;
  const boundedStart = clamp(
    start,
    BigInt(input.binding.start_step),
    BigInt(input.binding.end_step - 1),
  );
  const boundedEnd = clamp(
    end,
    boundedStart + 1n,
    BigInt(input.binding.end_step),
  );
  return { startStep: Number(boundedStart), endStep: Number(boundedEnd) };
}

function numericVector(
  value: unknown,
  expected: "SCALAR" | "VECTOR",
): readonly number[] | null {
  if (expected === "SCALAR") {
    return typeof value === "number" && Number.isFinite(value) ? [value] : null;
  }
  const record =
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const candidate = Array.isArray(value)
    ? value
    : Array.isArray(record?.position)
      ? record.position
      : Array.isArray(record?.positions)
        ? record.positions
        : Array.isArray(record?.values)
          ? record.values
          : Array.isArray(record?.position_xyz) &&
              Array.isArray(record?.orientation_wxyz)
            ? [...record.position_xyz, ...record.orientation_wxyz]
            : null;
  if (candidate === null || candidate.length === 0 || candidate.length > 4096)
    return null;
  const numeric = candidate.every(
    (item): item is number => typeof item === "number" && Number.isFinite(item),
  );
  return numeric ? candidate : null;
}

function xyzPoints(value: unknown): Float32Array | null {
  const flat = Array.isArray(value)
    ? value.every((item) => typeof item === "number")
      ? value
      : value.flatMap((item) => (Array.isArray(item) ? item : [Number.NaN]))
    : null;
  if (
    flat === null ||
    flat.length === 0 ||
    flat.length % 3 !== 0 ||
    flat.length > 150_000 ||
    !flat.every((item) => typeof item === "number" && Number.isFinite(item))
  )
    return null;
  return Float32Array.from(flat);
}

function eventLabel(value: unknown): string | null {
  if (typeof value === "string") return value.trim().slice(0, 256) || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return null;
  const record = value as Record<string, unknown>;
  for (const key of [
    "label",
    "type",
    "name",
    "source_format",
    "repository",
  ] as const) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim())
      return candidate.trim().slice(0, 256);
  }
  const episodeIndex = record.episode_index;
  if (typeof episodeIndex === "number" && Number.isFinite(episodeIndex))
    return `episode ${episodeIndex}`;
  return null;
}

/** Decode immutable logical samples identically for both physical storage formats. */
export function decodeStepRecords(
  records: readonly z.infer<typeof stepRecordWireSchema>[],
  binding: StepValueBinding,
  startStep: number,
  endStep: number,
): ViewerWindowPayload {
  const seen = new Set<number>();
  for (const record of records) {
    if (
      record.rollout_id !== binding.rollout_id ||
      record.step_index < startStep ||
      record.step_index >= endStep ||
      seen.has(record.step_index)
    ) {
      throw datasetDataError(
        "P06_WINDOW_RECORD_MISMATCH",
        "固定采集条目的步骤记录不一致。",
      );
    }
    seen.add(record.step_index);
  }
  if (!records.length)
    throw datasetDataError(
      "P06_STREAM_DATA_UNAVAILABLE",
      "当前时间范围没有可用的采集数据。",
      true,
    );

  const timestampsNs: string[] = [];
  const values: (readonly number[])[] = [];
  let series: readonly ViewerSeriesDescriptor[] | undefined;
  const pointFrames: ViewerPointFrame[] = [];
  const events: ViewerEventSample[] = [];
  for (const record of records) {
    const value = record.modalities[binding.modality_key];
    // sample_valid is the aggregate row flag: one missing camera can make it
    // false even when this numeric channel is valid.  Decode by the
    // channel-specific validity bit and omit only that channel's gaps.
    if (record.valid[binding.modality_key] === false) continue;
    timestampsNs.push(record.timestamp_ns);
    if (binding.value_kind === "POINTCLOUD_XYZ") {
      const points = xyzPoints(value);
      if (points === null)
        throw datasetDataError(
          "P06_POINTCLOUD_VALUE_INVALID",
          "点云样本不符合已声明的 XYZ 数据格式。",
        );
      pointFrames.push({ timestampNs: record.timestamp_ns, points });
      continue;
    }
    if (binding.value_kind === "EVENT") {
      const label = eventLabel(value);
      if (label === null)
        throw datasetDataError(
          "P06_EVENT_VALUE_INVALID",
          "事件样本不包含可显示的事件值。",
        );
      events.push({ timestampNs: record.timestamp_ns, label });
      continue;
    }
    const vector = numericVector(value, binding.value_kind);
    if (vector === null)
      throw datasetDataError(
        "P06_NUMERIC_VALUE_INVALID",
        "数值样本不符合固定数据流的声明格式。",
      );
    values.push(vector);
    // Preserve source joint names so URDF mappings use the same identities
    // as annotation, rather than assigning named vectors to J1, J2, ... .
    if (
      !series &&
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value)
    ) {
      const record = value as Record<string, unknown>;
      const names = record.name ?? record.names;
      if (
        Array.isArray(names) &&
        names.length === vector.length &&
        names.every((name) => typeof name === "string" && name.length > 0)
      ) {
        series = names.map((name, index) => ({
          id: `series-${index + 1}`,
          displayName: name,
          ...(Array.isArray(record.units) &&
          typeof record.units[index] === "string"
            ? { unit: record.units[index] }
            : {}),
        }));
      }
    }
  }

  if (!timestampsNs.length)
    throw datasetDataError(
      "P06_STREAM_DATA_UNAVAILABLE",
      "当前时间范围没有此通道的有效采集样本。",
      true,
    );

  return {
    generation: 0,
    timestampsNs,
    ...(values.length ? { values } : {}),
    ...(series ? { series } : {}),
    ...(pointFrames.length ? { pointFrames } : {}),
    ...(events.length ? { events } : {}),
  };
}
