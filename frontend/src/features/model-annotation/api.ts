import type { components } from "../../shared/api/generated/platform";
import { request, type RequestOptions } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  runtimeAnnotationScopeFromShell,
  type RuntimeAnnotationScope,
} from "../annotation/runtime/runtime-annotation-adapter";

export type ModelScope = RuntimeAnnotationScope;
export type Guide = components["schemas"]["Guide"];
export type Connection = components["schemas"]["ModelConnectionStatus"];
export type RunResult = components["schemas"]["InferenceResult"];

export function useModelScope() {
  const active = useShellStore((state) => state.scope);
  return runtimeAnnotationScopeFromShell(active);
}

export function modelRequest<T>(
  scope: ModelScope,
  path: string,
  options: Omit<RequestOptions, "scope" | "path">,
) {
  const base = `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/model-annotation`;
  return request<T>({
    ...options,
    scope,
    path: base + path,
    cache: "no-store",
  });
}
