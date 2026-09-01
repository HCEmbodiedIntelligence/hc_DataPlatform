import type {
  StreamDescriptor,
  ViewerSeriesDescriptor,
  ViewerWindowPayload,
  ViewerWindowSource,
} from "../../features/viewer";
import { createDomainError } from "../../shared/api/domain-error";
import type { RecordingGateway, RecordingScope } from "./api";

interface JointVector {
  readonly values: readonly number[];
  readonly names?: readonly string[];
}

function rawJointError(code: string, message: string, retryable = false) {
  return createDomainError({
    code: "PRECONDITION_FAILED",
    problemCode: code,
    message,
    fieldErrors: [],
    operationErrors: [{ code, message }],
    blockedReasons: [],
    requestId: null,
    retryable,
    httpStatus: null,
  });
}

function numericVector(value: unknown): readonly number[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > 256)
    return null;
  return value.every(
    (item): item is number => typeof item === "number" && Number.isFinite(item),
  )
    ? value
    : null;
}

export function recordingJointVector(value: unknown): JointVector | null {
  let candidate = value;
  if (typeof candidate === "string") {
    try {
      candidate = JSON.parse(candidate) as unknown;
    } catch {
      return null;
    }
  }
  const direct = numericVector(candidate);
  if (direct) return { values: direct };
  if (
    typeof candidate !== "object" ||
    candidate === null ||
    Array.isArray(candidate)
  )
    return null;
  const record = candidate as Record<string, unknown>;
  const positions = numericVector(record.position ?? record.positions);
  if (!positions) return null;
  const namesValue = record.name ?? record.names;
  const names = Array.isArray(namesValue) ? namesValue : null;
  const validNames =
    names?.length === positions.length &&
    names.every(
      (name): name is string => typeof name === "string" && name.length > 0,
    )
      ? names
      : undefined;
  return { values: positions, ...(validNames ? { names: validNames } : {}) };
}

function seriesForVector(
  vector: JointVector,
): readonly ViewerSeriesDescriptor[] {
  return vector.values.map((_, index) => ({
    id: `joint-${index + 1}`,
    displayName: vector.names?.[index] ?? `J${index + 1}`,
    unit: "rad",
  }));
}

function createRawJointWindowSource(input: {
  readonly gateway: RecordingGateway;
  readonly scope: RecordingScope;
  readonly recordingId: string;
}): ViewerWindowSource {
  return {
    async loadWindow(window, signal): Promise<ViewerWindowPayload> {
      const response = await input.gateway.sensorWindow(
        input.scope,
        input.recordingId,
        {
          startOffsetNs: window.startNs,
          endOffsetNs: window.endNs,
          maximumSamples: 10_000,
        },
        signal,
      );
      if (
        response.recording_id !== input.recordingId ||
        response.start_offset_ns !== window.startNs ||
        response.end_offset_ns !== window.endNs
      )
        throw rawJointError(
          "P23_RAW_JOINT_WINDOW_IDENTITY_MISMATCH",
          "原始关节角窗口与当前录制或请求时间范围不一致。",
        );
      if (response.truncated)
        throw rawJointError(
          "P23_RAW_JOINT_WINDOW_TRUNCATED",
          "当前原始关节角窗口样本过多，请缩小时间范围。",
          true,
        );

      let series: readonly ViewerSeriesDescriptor[] | null = null;
      let vectorSize: number | null = null;
      const timestampsNs: string[] = [];
      const values: (readonly number[])[] = [];
      for (const sample of response.samples) {
        const vector = recordingJointVector(sample.value);
        if (!vector) continue;
        if (vectorSize !== null && vector.values.length !== vectorSize)
          throw rawJointError(
            "P23_RAW_JOINT_SAMPLE_INVALID",
            "原始关节角窗口包含维度不一致的样本。",
            true,
          );
        vectorSize = vector.values.length;
        series ??= seriesForVector(vector);
        timestampsNs.push(sample.offset_ns);
        values.push(vector.values);
      }
      if (!values.length)
        throw rawJointError(
          "P23_RAW_JOINT_WINDOW_EMPTY",
          `原始 Topic ${response.topic} 在当前时间范围没有有效关节角样本。`,
          true,
        );
      return {
        generation: 0,
        timestampsNs,
        values,
        series: series ?? [],
      };
    },
  };
}

export function buildRecordingJointAngleStream(input: {
  readonly gateway: RecordingGateway;
  readonly scope: RecordingScope;
  readonly recordingId: string;
  readonly durationNs: string;
}): StreamDescriptor {
  return {
    id: `p23-raw-joint-angle-${input.recordingId}`,
    canonicalPath: "/robot/joint_states",
    displayName: "原始关节角",
    modality: "joint_state",
    schema: {
      id: "raw-recording-joint-state",
      version: "recording-sensor-window/v1",
      unit: "rad",
    },
    rateHz: 50,
    startNs: "0",
    endNs: input.durationNs,
    availability: "ready",
    accessibleSummary:
      "直接从连续录制 SENSOR_DATA MCAP 读取的原始关节角；不依赖 Episode、QC、对齐或 Lance。",
    windowSource: createRawJointWindowSource(input),
  };
}
