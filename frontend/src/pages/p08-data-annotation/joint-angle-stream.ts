import { z } from "zod";
import type {
  StreamDescriptor,
  ViewerSeriesDescriptor,
  ViewerWindow,
  ViewerWindowPayload,
  ViewerWindowSource,
} from "../../features/viewer";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import type {
  RuntimeAnnotationBundle,
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
} from "./runtime-annotation-adapter";

export { buildJointFrameSource as buildRuntimeJointFrameSource } from "../../features/viewer";
import {
  normalizeStepRateHz,
  stepToTimelineNs,
  timelineNsToStep,
} from "./runtime-annotation-adapter";

const decimalNsWireSchema = z.string().regex(/^(0|[1-9][0-9]*)$/u);
const safeStepWireSchema = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);

const annotationJointStepWireSchema = z
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

const annotationJointWindowWireSchema = z
  .object({
    schema_version: z.string().min(1),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.number().int().positive(),
    rollout_id: z.string().min(1),
    start_step: safeStepWireSchema,
    end_step: safeStepWireSchema,
    steps: z.array(annotationJointStepWireSchema),
  })
  .strict();

interface JointVector {
  readonly values: readonly number[];
  readonly names?: readonly string[];
}

function jointDataError(
  problemCode: string,
  message: string,
  retryable = false,
) {
  return createDomainError({
    code: "PRECONDITION_FAILED",
    problemCode,
    message,
    fieldErrors: [],
    operationErrors: [{ code: problemCode, message }],
    blockedReasons: [],
    requestId: null,
    retryable,
    httpStatus: null,
  });
}

function numericVector(value: unknown): readonly number[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > 64)
    return null;
  return value.every(
    (item): item is number => typeof item === "number" && Number.isFinite(item),
  )
    ? value
    : null;
}

function jointVector(value: unknown): JointVector | null {
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

function modalityCandidates(topicName: string): readonly string[] {
  const withoutLeadingSlash = topicName.replace(/^\/+/, "");
  const dotted = withoutLeadingSlash.replaceAll("/", ".");
  const leaf = withoutLeadingSlash.split("/").at(-1) ?? withoutLeadingSlash;
  return [
    topicName,
    withoutLeadingSlash,
    dotted,
    leaf,
    "joint.position",
    "joint_positions",
    "joint",
  ];
}

function jointVectorFromModalities(
  modalities: Readonly<Record<string, unknown>>,
  topicName: string,
): { readonly key: string; readonly vector: JointVector } | null {
  for (const key of modalityCandidates(topicName)) {
    const vector = jointVector(modalities[key]);
    if (vector) return { key, vector };
  }
  const semanticMatches = Object.entries(modalities)
    .map(([key, value]) => ({ key, vector: jointVector(value) }))
    .filter(
      (
        entry,
      ): entry is { readonly key: string; readonly vector: JointVector } =>
        entry.vector !== null &&
        /joint(?:[^a-z0-9]*(?:state|position|angle))?|(?:position|angle)[^a-z0-9]*joint/iu.test(
          entry.key,
        ),
    );
  return semanticMatches.length === 1 ? semanticMatches[0]! : null;
}

function stepWindowForTime(
  window: ViewerWindow,
  stepCount: number,
  frequencyHz: number,
): { readonly startStep: number; readonly endStep: number } {
  const boundedCount = Math.max(1, stepCount);
  const start = timelineNsToStep(window.startNs, frequencyHz);
  const inclusiveEndNs = BigInt(window.endNs) - 1n;
  const end =
    inclusiveEndNs < 0n
      ? 0
      : timelineNsToStep(inclusiveEndNs.toString(), frequencyHz) + 1;
  const startStep = Math.max(0, Math.min(boundedCount - 1, start));
  const endStep = Math.max(startStep + 1, Math.min(boundedCount, end));
  return { startStep, endStep };
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

function createAnnotationJointWindowSource(input: {
  readonly scope: RuntimeAnnotationScope;
  readonly task: RuntimeAnnotationTask;
  readonly topicName: string;
  readonly stepCount: number;
  readonly frequencyHz: number;
}): ViewerWindowSource {
  return {
    async loadWindow(
      viewerWindow: ViewerWindow,
      signal: AbortSignal,
    ): Promise<ViewerWindowPayload> {
      const { startStep, endStep } = stepWindowForTime(
        viewerWindow,
        input.stepCount,
        input.frequencyHz,
      );
      const endpoint = `/projects/${encodeURIComponent(input.task.project_id)}/datasets/${encodeURIComponent(input.task.dataset_id)}/rollouts/${encodeURIComponent(input.task.rollout_id)}/steps`;
      const raw = await request<unknown>({
        method: "GET",
        path: endpoint,
        scope: {
          organizationId: input.scope.organizationId,
          projectId: input.scope.projectId,
          regionCode: input.scope.regionCode,
        },
        cache: "no-store",
        signal,
        query: {
          startStep,
          endStep,
          version: input.task.base_lance_version,
        },
      });
      const window = parseWire(annotationJointWindowWireSchema, raw, {
        endpoint,
      });
      if (
        window.project_id !== input.task.project_id ||
        window.dataset_id !== input.task.dataset_id ||
        window.dataset_version !== input.task.base_lance_version ||
        window.rollout_id !== input.task.rollout_id ||
        window.start_step !== startStep ||
        window.end_step !== endStep
      )
        throw jointDataError(
          "P08_JOINT_WINDOW_IDENTITY_MISMATCH",
          "关节角窗口与当前固定任务身份不一致。",
        );
      if (!window.steps.length)
        throw jointDataError(
          "P08_JOINT_WINDOW_EMPTY",
          "当前时间范围没有可用的关节角样本。",
          true,
        );

      let resolvedKey: string | null = null;
      let resolvedSeries: readonly ViewerSeriesDescriptor[] | null = null;
      let vectorSize: number | null = null;
      const values: (readonly number[])[] = [];
      const timestampsNs: string[] = [];
      const seen = new Set<number>();
      for (const step of window.steps) {
        if (
          step.rollout_id !== input.task.rollout_id ||
          step.step_index < startStep ||
          step.step_index >= endStep ||
          seen.has(step.step_index)
        )
          throw jointDataError(
            "P08_JOINT_WINDOW_RECORD_MISMATCH",
            "关节角步骤记录与请求窗口不一致。",
          );
        seen.add(step.step_index);
        let resolved: {
          readonly key: string;
          readonly vector: JointVector;
        } | null = null;
        if (resolvedKey) {
          const vector = jointVector(step.modalities[resolvedKey]);
          if (vector) resolved = { key: resolvedKey, vector };
        } else {
          resolved = jointVectorFromModalities(
            step.modalities,
            input.topicName,
          );
        }
        if (!resolved) {
          const expectedKey = resolvedKey ?? input.topicName;
          if (
            step.modalities[expectedKey] == null ||
            step.valid[expectedKey] === false
          )
            continue;
          throw jointDataError(
            "P08_JOINT_MODALITY_UNBOUND",
            `未能把 ${input.topicName} 唯一绑定到 Lance 关节角向量。`,
          );
        }
        if (step.valid[resolved.key] === false) continue;
        if (vectorSize !== null && resolved.vector.values.length !== vectorSize)
          throw jointDataError(
            "P08_JOINT_SAMPLE_INVALID",
            "当前窗口包含无效或维度不一致的关节角样本。",
            true,
          );
        resolvedKey = resolved.key;
        vectorSize = resolved.vector.values.length;
        resolvedSeries ??= seriesForVector(resolved.vector);
        values.push(resolved.vector.values);
        timestampsNs.push(stepToTimelineNs(step.step_index, input.frequencyHz));
      }
      if (!values.length)
        throw jointDataError(
          "P08_JOINT_WINDOW_EMPTY",
          "当前时间范围没有有效的关节角样本。",
          true,
        );
      return {
        generation: 0,
        timestampsNs,
        values,
        series: resolvedSeries ?? [],
      };
    },
  };
}

export function buildRuntimeJointAngleStream(input: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly scope: RuntimeAnnotationScope;
}): StreamDescriptor | null {
  const topic = input.bundle.manifest?.topics.find((candidate) =>
    /(^|[/_.-])joint([/_\s.-]|$)/iu.test(candidate.name),
  );
  if (!topic) return null;
  const stepCount = input.bundle.task.base_step_count ?? 0;
  const frequencyHz = normalizeStepRateHz(
    input.bundle.datasetVersion.frequency_hz,
  );
  const endNs = stepToTimelineNs(Math.max(1, stepCount), frequencyHz);
  return {
    id: `p08-joint-angle-${input.bundle.task.task_id}`,
    canonicalPath: topic.name,
    displayName: "关节角变化",
    modality: "joint_state",
    schema: {
      id: topic.schema_name ?? "manifest-joint-state",
      version: String(input.bundle.task.base_lance_version),
      unit: "rad",
    },
    rateHz: frequencyHz,
    startNs: "0",
    endNs,
    availability: stepCount > 0 ? "ready" : "missing",
    accessibleSummary:
      stepCount > 0
        ? `${topic.name} 的真实关节角向量，按固定 Lance v${input.bundle.task.base_lance_version} 读取并与共享光标同步。`
        : `${topic.name} 已发现，但任务未返回可读取的步骤范围。`,
    ...(stepCount > 0
      ? {
          windowSource: createAnnotationJointWindowSource({
            scope: input.scope,
            task: input.bundle.task,
            topicName: topic.name,
            stepCount,
            frequencyHz,
          }),
        }
      : {}),
  };
}
