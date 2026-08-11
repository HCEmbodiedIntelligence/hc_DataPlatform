import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useShellStore } from '../../../shared/scope/shell-store';
import {
  createCleaningDraftFromManualIssueCommand,
  resolveManualIssueCommand,
  triageManualIssueCommand,
  type CreateDraftFromManualIssueCommandInput,
  type ResolveManualIssueCommandInput,
  type TriageManualIssueCommandInput,
} from './manual-issues.commands';
import { manualIssueKeys } from './query-keys';

type ScopeFields = 'organizationId' | 'projectId' | 'regionCode';

function useRequiredScope() {
  const scope = useShellStore((state) => state.scope);
  return {
    organizationId: scope?.organizationId ?? null,
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

function assertScope(scope: ReturnType<typeof useRequiredScope>) {
  if (!scope.organizationId || !scope.projectId || !scope.regionCode) {
    throw new Error('CLEANING_SCOPE_REQUIRED');
  }
  return {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  };
}

function invalidateManualIssue(client: ReturnType<typeof useQueryClient>, manualIssueId: string): void {
  void client.invalidateQueries({ queryKey: manualIssueKeys.detail(manualIssueId) });
  void client.invalidateQueries({
    predicate: (query) => query.queryKey[0] === 'manual-issues' &&
      (query.queryKey[2] === 'list' || query.queryKey[2] === 'page'),
  });
}

export type TriageManualIssueIntent = Omit<TriageManualIssueCommandInput, ScopeFields>;

export function useTriageManualIssue() {
  const scope = useRequiredScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: TriageManualIssueIntent) =>
      triageManualIssueCommand({ ...assertScope(scope), ...intent }),
    onSuccess: (_issue, intent) => invalidateManualIssue(client, intent.manualIssueId),
  });
}

export type ResolveManualIssueIntent = Omit<ResolveManualIssueCommandInput, ScopeFields>;

export function useResolveManualIssue() {
  const scope = useRequiredScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: ResolveManualIssueIntent) =>
      resolveManualIssueCommand({ ...assertScope(scope), ...intent }),
    onSuccess: (_issue, intent) => invalidateManualIssue(client, intent.manualIssueId),
  });
}

export type CreateDraftFromManualIssueIntent = Omit<CreateDraftFromManualIssueCommandInput, ScopeFields>;

/** P09-owned linkage only. It never invalidates or patches ReviewFinding caches. */
export function useCreateDraftFromManualIssue() {
  const scope = useRequiredScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CreateDraftFromManualIssueIntent) =>
      createCleaningDraftFromManualIssueCommand({ ...assertScope(scope), ...intent }),
    onSuccess: (_result, intent) => {
      invalidateManualIssue(client, intent.manualIssueId);
      void client.invalidateQueries({
        predicate: (query) => query.queryKey[0] === 'cleaning' &&
          (query.queryKey[2] === 'draft-list' || query.queryKey[2] === 'draft-summary'),
      });
    },
  });
}
