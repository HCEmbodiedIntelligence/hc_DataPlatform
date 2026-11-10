import type { QueryClient } from "@tanstack/react-query";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { preloadAuthorizedModel } from "../../viewer/authorized-model-cache";
import {
  listRuntimeAnnotationTasks, loadRuntimeAnnotationBundle,
  type RuntimeAnnotationScope, type RuntimeAnnotationTask, type RuntimeAnnotationBundle, type AnnotationWorkbenchMode,
} from "./runtime-annotation-adapter";

export function annotationTaskListKey(scope: RuntimeAnnotationScope, revision: string) {
  return ["p08-runtime-annotation-list", scope.organizationId, revision, scope.projectId, scope.regionCode] as const;
}

export function taskQueryKey(scope: RuntimeAnnotationScope, taskId: string, mode: AnnotationWorkbenchMode, revision: string) {
  return ["p08-runtime-annotation", scope.organizationId, revision, scope.projectId, scope.regionCode, taskId, mode] as const;
}

/** Publish a mutation result to both modes and every cached navigator in this scope. */
export function synchronizeAnnotationBundle(
  client: QueryClient, scope: RuntimeAnnotationScope, revision: string,
  mode: AnnotationWorkbenchMode, updated: RuntimeAnnotationBundle,
) {
  const key = taskQueryKey(scope, updated.task.task_id, mode, revision);
  const cachedBundles = client.getQueriesData<RuntimeAnnotationBundle>({ queryKey: key.slice(0, 5) });
  // A delayed read or a cached route must not roll a successful mutation back.
  for (const [, cached] of cachedBundles) {
    if (cached?.task.task_id === updated.task.task_id && cached.task.state_version > updated.task.state_version)
      updated = { ...updated, task: cached.task, history: cached.history, draft: cached.draft };
  }
  const listKey = annotationTaskListKey(scope, revision);
  const latestTasks = new Map<string, RuntimeAnnotationTask>();
  for (const task of [
    ...updated.tasks,
    ...(client.getQueryData<readonly RuntimeAnnotationTask[]>(listKey) ?? []),
    ...cachedBundles.flatMap(([, cached]) => cached ? [cached.task] : []),
    updated.task,
  ]) {
    if ((latestTasks.get(task.task_id)?.state_version ?? -1) <= task.state_version)
      latestTasks.set(task.task_id, task);
  }
  const mergeTasks = (tasks: readonly RuntimeAnnotationTask[]) => tasks.map(task => {
    const latest = latestTasks.get(task.task_id);
    return latest && latest.state_version >= task.state_version ? latest : task;
  });
  client.setQueryData<readonly RuntimeAnnotationTask[]>(listKey,
    tasks => mergeTasks(tasks ?? updated.tasks));
  for (const [cachedKey, cached] of cachedBundles) {
    if (!cached) continue;
    const sameTask = cached.task.task_id === updated.task.task_id;
    const current = sameTask && cached.task.state_version <= updated.task.state_version;
    const draft = cachedKey[6] === "annotation"
      ? updated.draft ?? (cached.draft?.etag === updated.task.etag ? cached.draft : null)
      : null;
    client.setQueryData(cachedKey, {
      ...cached,
      ...(current ? { task: updated.task, history: updated.history, draft } : {}),
      tasks: mergeTasks(cached.tasks),
    });
    // A reviewer can observe a returned draft without permission to read it.
    // Entering annotation must fetch that draft before enabling editing.
    if (current && cachedKey[6] === "annotation" && !draft &&
        (updated.task.status === "DRAFT" || updated.task.status === "NEEDS_REVISION"))
      void client.invalidateQueries({ queryKey: cachedKey, exact: true, refetchType: "none" });
  }
  const currentDraft = client.getQueryData<RuntimeAnnotationBundle>(key)?.draft;
  const draft = mode === "annotation"
    ? updated.draft ?? (currentDraft?.etag === updated.task.etag ? currentDraft : null)
    : null;
  client.setQueryData(key, { ...updated, draft, tasks: mergeTasks(updated.tasks) });
  if (mode === "annotation" && !draft &&
      (updated.task.status === "DRAFT" || updated.task.status === "NEEDS_REVISION"))
    void client.invalidateQueries({ queryKey: key, exact: true, refetchType: "none" });
}

export function taskQueryOptions(scope: RuntimeAnnotationScope, taskId: string, mode: AnnotationWorkbenchMode, revision: string, tasks?: readonly RuntimeAnnotationTask[]) {
  return {
    queryKey: taskQueryKey(scope, taskId, mode, revision),
    queryFn: ({ signal }: { signal: AbortSignal }) => loadRuntimeAnnotationBundle(scope, taskId, mode, signal, tasks),
    staleTime: 15_000, gcTime: 120_000, retry: false as const,
  };
}

/** Warm a common model from the annotation queue without creating a WebGL renderer. */
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
