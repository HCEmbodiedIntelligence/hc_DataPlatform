import { z } from "zod";
import type { Scope } from "../../entities/scope";
import {
  episodeDataReferenceWireSchema,
  episodePackageBindingWireSchema,
  type EpisodePackageBinding,
} from "../../features/datasets/api/episode-package-wire";
import type { ViewerWindowSource } from "../../features/viewer";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import {
  decodeStepRecords,
  datasetDataError,
  safeStepWireSchema,
  stepRecordWireSchema,
  stepWindowForTime,
} from "../../features/datasets/episodes/step-window";

const packageWindowSchema = z
  .object({
    data: episodeDataReferenceWireSchema,
    start_step: safeStepWireSchema,
    end_step: safeStepWireSchema,
    steps: z.array(stepRecordWireSchema).max(4096),
  })
  .strict();

/** Reads the pinned Episode version; no dataset head or Lance number is inferred. */
export function createDatasetPackageWindowSource(input: {
  readonly scope: Scope;
  readonly datasetId: string;
  readonly streamStartNs: string;
  readonly streamEndNs: string;
  readonly binding: EpisodePackageBinding;
}): ViewerWindowSource {
  const binding = episodePackageBindingWireSchema.parse(input.binding);
  const identity = binding.data.identity;
  if (
    identity.organization_id !== input.scope.organizationId ||
    identity.project_id !== input.scope.projectId ||
    identity.region_code !== input.scope.regionCode ||
    identity.dataset_id !== input.datasetId ||
    binding.value_kind === null
  ) {
    throw datasetDataError(
      "P06_PACKAGE_BINDING_MISMATCH",
      "数据绑定与当前作用域或数值通道不一致。",
    );
  }
  const numericBinding = { ...binding, value_kind: binding.value_kind };
  const endpoint = `/projects/${encodeURIComponent(identity.project_id)}/regions/${encodeURIComponent(identity.region_code)}/datasets/${encodeURIComponent(identity.dataset_id)}/episodes/${encodeURIComponent(identity.episode_id)}/data-versions/${encodeURIComponent(binding.data.data_version_id)}/steps`;
  // Zod parses both sides into the same strict shape/order, including all scope,
  // hash, schema, physical format and frame-count fields of this immutable ref.
  const expectedData = JSON.stringify(binding.data);
  return {
    async loadWindow(viewerWindow, signal) {
      const { startStep, endStep } = stepWindowForTime(viewerWindow, input);
      const records: z.infer<typeof stepRecordWireSchema>[] = [];
      let previousTimestamp: bigint | undefined;
      for (let start = startStep; start < endStep; start += 4096) {
        signal.throwIfAborted();
        const end = Math.min(start + 4096, endStep);
        const raw = await request<unknown>({
          method: "GET",
          path: endpoint,
          scope: input.scope,
          cache: "no-store",
          signal,
          query: {
            startStep: start,
            endStep: end,
            columns: [binding.modality_key],
          },
        });
        signal.throwIfAborted();
        const page = parseWire(packageWindowSchema, raw, { endpoint });
        if (
          JSON.stringify(page.data) !== expectedData ||
          page.start_step !== start ||
          page.end_step !== end
        ) {
          throw datasetDataError(
            "P06_WINDOW_IDENTITY_MISMATCH",
            "数据窗口与固定的 Episode 数据版本不一致。",
          );
        }
        if (page.steps.length !== end - start) {
          throw datasetDataError(
            "P06_WINDOW_RECORD_MISMATCH",
            "标准包的数据窗口不完整。",
          );
        }
        page.steps.forEach((record, index) => {
          const timestamp = BigInt(record.timestamp_ns);
          if (
            record.step_index !== start + index ||
            record.rollout_id !== binding.rollout_id ||
            timestamp < BigInt(input.streamStartNs) ||
            timestamp >= BigInt(input.streamEndNs) ||
            (previousTimestamp !== undefined &&
              timestamp <= previousTimestamp) ||
            !(binding.modality_key in record.modalities) ||
            !(binding.modality_key in record.valid)
          ) {
            throw datasetDataError(
              "P06_WINDOW_RECORD_MISMATCH",
              "标准包的步骤、时间轴或通道记录不一致。",
            );
          }
          previousTimestamp = timestamp;
          records.push(record);
        });
      }
      return decodeStepRecords(records, numericBinding, startStep, endStep);
    },
  };
}
