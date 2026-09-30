import { z } from "zod";
import type { Scope } from "../../entities/scope";
import type {
  ViewerWindow,
  ViewerWindowPayload,
  ViewerWindowSource,
} from "../../features/viewer";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import {
  decodeStepRecords,
  datasetDataError,
  safeStepWireSchema,
  stepRecordWireSchema,
  stepWindowForTime,
  type StepValueBinding,
} from "../../features/datasets/episodes/step-window";

export interface EpisodeDataBinding extends StepValueBinding {
  readonly lance_version: number;
}

const stepWindowWireSchema = z
  .object({
    schema_version: z.string().min(1),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.number().int().positive(),
    rollout_id: z.string().min(1),
    start_step: safeStepWireSchema,
    end_step: safeStepWireSchema,
    steps: z.array(stepRecordWireSchema),
  })
  .strict();

function assertWindowIdentity(
  window: z.infer<typeof stepWindowWireSchema>,
  input: {
    readonly scope: Scope;
    readonly datasetId: string;
    readonly binding: EpisodeDataBinding;
    readonly startStep: number;
    readonly endStep: number;
  },
): void {
  if (
    window.project_id !== input.scope.projectId ||
    window.dataset_id !== input.datasetId ||
    window.dataset_version !== input.binding.lance_version ||
    window.rollout_id !== input.binding.rollout_id ||
    window.start_step !== input.startStep ||
    window.end_step !== input.endStep
  ) {
    throw datasetDataError(
      "P06_WINDOW_IDENTITY_MISMATCH",
      "固定采集条目的数据窗口与服务端响应不一致。",
    );
  }
}

/**
 * Returns a lazy, scoped source for non-camera P06 panels.  It reads only an
 * explicit non-camera binding from the immutable revision document; no viewer
 * code derives rollouts, names, physical paths or object-store credentials.
 */
export function createDatasetLanceWindowSource(input: {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly streamStartNs: string;
  readonly streamEndNs: string;
  readonly binding: EpisodeDataBinding;
}): ViewerWindowSource {
  return {
    async loadWindow(
      viewerWindow: ViewerWindow,
      signal: AbortSignal,
    ): Promise<ViewerWindowPayload> {
      const { startStep, endStep } = stepWindowForTime(viewerWindow, input);
      const endpoint = `/projects/${encodeURIComponent(input.scope.projectId ?? "")}/datasets/${encodeURIComponent(input.datasetId)}/rollouts/${encodeURIComponent(input.binding.rollout_id)}/steps`;
      const raw = await request<unknown>({
        method: "GET",
        path: endpoint,
        scope: input.scope,
        cache: "no-store",
        signal,
        query: {
          startStep,
          endStep,
          version: input.binding.lance_version,
          columns: [input.binding.modality_key],
        },
      });
      const window = parseWire(stepWindowWireSchema, raw, { endpoint });
      assertWindowIdentity(window, { ...input, startStep, endStep });

      return decodeStepRecords(window.steps, input.binding, startStep, endStep);
    },
  };
}
