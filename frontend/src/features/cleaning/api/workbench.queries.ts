import { useQuery } from '@tanstack/react-query';
import { request } from '../../../shared/api/http-client';
import { useShellStore } from '../../../shared/scope/shell-store';
import { adaptCleaningReviewFeedback, adaptCleaningWorkbench } from './workbench.adapter';
import { cleaningDraftKeys, readonlyReviewFindingKeys } from './query-keys';
import { reviewFindingsEnvelopeWireSchema } from './workbench.schemas';

function useScope() {
  const scope = useShellStore((state) => state.scope);
  return {
    organizationId: scope?.organizationId ?? null,
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

function draftPath(projectId: string, regionCode: string, draftId: string, suffix = ''): string {
  return `/projects/${encodeURIComponent(projectId)}/regions/${encodeURIComponent(regionCode)}/cleaning-drafts/${encodeURIComponent(draftId)}${suffix}`;
}

export function useCleaningDraftBootstrap(draftId: string | undefined, allowed = true) {
  const scope = useScope();
  return useQuery({
    queryKey: cleaningDraftKeys.bootstrap(draftId ?? 'invalid'),
    enabled: allowed && Boolean(draftId && scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 5_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET',
        path: draftPath(scope.projectId!, scope.regionCode!, draftId!, '/bootstrap'),
        signal,
      });
      return adaptCleaningWorkbench(raw, {
        organizationId: scope.organizationId!, projectId: scope.projectId!, regionCode: scope.regionCode!,
      }, draftId!);
    },
  });
}

/** Read-only P07-owned feedback. No Review mutation is exported from this feature. */
export function useReadonlyCleaningReviewFindings(draftId: string | undefined, decisionId: string | undefined, allowed = true) {
  const scope = useScope();
  return useQuery({
    queryKey: readonlyReviewFindingKeys.decision(decisionId ?? 'invalid'),
    enabled: allowed && Boolean(draftId && decisionId && scope.projectId && scope.regionCode),
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET',
        path: draftPath(scope.projectId!, scope.regionCode!, draftId!, '/review-findings'),
        signal,
      });
      return adaptCleaningReviewFeedback(reviewFindingsEnvelopeWireSchema.parse(raw).data.feedback);
    },
  });
}
