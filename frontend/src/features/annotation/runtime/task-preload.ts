import type { QueryClient } from "@tanstack/react-query";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { preloadAuthorizedModel } from "../../viewer/authorized-model-cache";
import {
  listRuntimeAnnotationTasks, loadRuntimeAnnotationBundle,
  type RuntimeAnnotationScope, type RuntimeAnnotationTask, type AnnotationWorkbenchMode,
} from "./runtime-annotation-adapter";

export function annotationTaskListKey(scope: RuntimeAnnotationScope, revision: string) {
  return ["p08-runtime-annotation-list", scope.organizationId, revision, scope.projectId, scope.regionCode] as const;
}

export function taskQueryKey(scope: RuntimeAnnotationScope, taskId: string, mode: AnnotationWorkbenchMode, revision: string) {
  return ["p08-runtime-annotation", scope.organizationId, revision, scope.projectId, scope.regionCode, taskId, mode] as const;
}

export function taskQueryOptions(scope: RuntimeAnnotationScope, taskId: string, mode: AnnotationWorkbenchMode, revision: string, tasks?: readonly RuntimeAnnotationTask[]) {
  return {
    queryKey: taskQueryKey(scope, taskId, mode, revision),
    queryFn: ({ signal }: { signal: AbortSignal }) => loadRuntimeAnnotationBundle(scope, taskId, mode, signal, tasks),
    staleTime: 15_000, gcTime: 120_000, retry: false as const,
  };
}

/** One common model is warmed after login, without creating a WebGL renderer. */
export async function preloadHomeModel(client: QueryClient, scope: RuntimeAnnotationScope, revision: string, signal: AbortSignal) {
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    const tasks = await client.fetchQuery({
      queryKey: annotationTaskListKey(scope, revision),
      queryFn: ({ signal }) => listRuntimeAnnotationTasks(scope, signal),
      staleTime: 15_000, retry: false,
    });
    lease.signal.throwIfAborted();
    const first = tasks[0];
    if (!first) return;
    const bundle = await client.fetchQuery(taskQueryOptions(scope, first.task_id, "annotation", revision, tasks));
    lease.signal.throwIfAborted();
    if (bundle.logical?.robotAssets?.length)
      await preloadAuthorizedModel(bundle.logical.robotAssets, scope, lease.signal);
  } finally { lease.release(); }
}
