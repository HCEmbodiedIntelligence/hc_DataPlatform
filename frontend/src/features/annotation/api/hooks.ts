import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  claimAnnotationTask,
  getAnnotationTaskDetail,
  listAnnotationTasks,
  materializeAnnotationTaskEntry,
  preflightAnnotationSubmission,
  rebaseAnnotationTask,
  resolveAnnotationTaskEntry,
  reviewAnnotationTask,
  saveAnnotationDraft,
  submitAnnotationTask,
} from './client';
import type { AnnotationEntryContext, AnnotationListFilters, AnnotationScope } from './client';
import type { AnnotationSchemaOption } from '../handoff/AnnotationEntryAction';
import { annotationQueryKeys } from './query-keys';

export function useAnnotationTasks(scope: AnnotationScope | null, filters: AnnotationListFilters, enabled = true) {
  return useQuery({
    queryKey: scope ? annotationQueryKeys.list(scope, filters) : ['annotation', 'disabled', 'tasks'],
    queryFn: ({ signal }) => listAnnotationTasks(scope!, filters, signal),
    enabled: enabled && scope !== null,
    staleTime: 15_000,
  });
}

export function useAnnotationTask(scope: AnnotationScope | null, taskId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: scope && taskId ? annotationQueryKeys.task(scope, taskId) : ['annotation', 'disabled', 'task'],
    queryFn: ({ signal }) => getAnnotationTaskDetail(scope!, taskId!, signal),
    enabled: enabled && scope !== null && !!taskId,
    staleTime: 0,
  });
}

export function useAnnotationEntryResolution(context: AnnotationEntryContext | null, enabled = true) {
  return useQuery({
    queryKey: context ? annotationQueryKeys.entryResolution(context) : ['annotation', 'disabled', 'entry-resolution'],
    queryFn: ({ signal }) => resolveAnnotationTaskEntry(context!, signal),
    enabled: enabled && context !== null,
    staleTime: 5_000,
  });
}

export function useMaterializeAnnotationTaskEntry(scope: AnnotationScope) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { option: AnnotationSchemaOption; clientSessionId: string; idempotencyKey: string }) =>
      materializeAnnotationTaskEntry(scope, input.option, input.clientSessionId, input.idempotencyKey),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: annotationQueryKeys.lists(scope) }),
  });
}

function useTaskMutation<TVariables, TResult>(
  scope: AnnotationScope,
  taskId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: annotationQueryKeys.task(scope, taskId) }),
        queryClient.invalidateQueries({ queryKey: annotationQueryKeys.lists(scope) }),
      ]);
    },
  });
}

export function useClaimAnnotationTask(scope: AnnotationScope, taskId: string) {
  return useTaskMutation(scope, taskId, (variables: Parameters<typeof claimAnnotationTask>[0] & { clientSessionId: string }) =>
    claimAnnotationTask(variables, variables.clientSessionId));
}

export function useSaveAnnotationDraft(scope: AnnotationScope, taskId: string) {
  return useTaskMutation(scope, taskId, (variables: Parameters<typeof saveAnnotationDraft>[0] & Parameters<typeof saveAnnotationDraft>[1]) =>
    saveAnnotationDraft(variables, variables));
}

export function usePreflightAnnotationSubmission(scope: AnnotationScope, taskId: string) {
  return useMutation({
    mutationKey: annotationQueryKeys.task(scope, taskId),
    mutationFn: (variables: Parameters<typeof preflightAnnotationSubmission>[0] & Parameters<typeof preflightAnnotationSubmission>[1]) =>
      preflightAnnotationSubmission(variables, variables),
  });
}

export function useSubmitAnnotationTask(scope: AnnotationScope, taskId: string) {
  return useTaskMutation(scope, taskId, (variables: Parameters<typeof submitAnnotationTask>[0] & Parameters<typeof submitAnnotationTask>[1]) =>
    submitAnnotationTask(variables, variables));
}

export function useReviewAnnotationTask(scope: AnnotationScope, taskId: string) {
  return useTaskMutation(scope, taskId, (variables: Parameters<typeof reviewAnnotationTask>[0] & Parameters<typeof reviewAnnotationTask>[1]) =>
    reviewAnnotationTask(variables, variables));
}

export function useRebaseAnnotationTask(scope: AnnotationScope, taskId: string) {
  return useTaskMutation(scope, taskId, (variables: Parameters<typeof rebaseAnnotationTask>[0] & Parameters<typeof rebaseAnnotationTask>[1]) =>
    rebaseAnnotationTask(variables, variables));
}
