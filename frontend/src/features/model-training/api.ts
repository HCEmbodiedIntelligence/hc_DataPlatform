import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type { Scope } from "../../shared/scope/model";

type Schemas = components["schemas"];
export type TrainingRun = Schemas["TrainingRun"];
export type TrainingOption = Schemas["TrainingOption"];
export type TrainingStart = Schemas["TrainingStart"];

interface Responses {
  datasets: Schemas["TrainingOptions"];
  models: Schemas["TrainingOptions"];
  cards: Schemas["TrainingOptions"];
  configs: Schemas["TrainingConfigs"];
  runs: Schemas["TrainingRunPage"];
}

function base(scope: Scope) {
  if (!scope.projectId || !scope.regionCode)
    throw new Error("请选择项目和区域。");
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/model-training`;
}

export function readTraining<K extends keyof Responses>(
  scope: Scope,
  resource: K,
  query: Record<string, string | number>,
  signal: AbortSignal,
) {
  return request<Responses[K]>({
    method: "GET",
    path: `${base(scope)}/${resource}`,
    scope,
    query,
    signal,
    cache: "no-store",
  });
}

export function startTraining(
  scope: Scope,
  intent: { command: TrainingStart; key: string },
) {
  return request<TrainingRun>({
    method: "POST",
    path: `${base(scope)}/runs`,
    scope,
    body: intent.command,
    idempotencyKey: intent.key,
  });
}
