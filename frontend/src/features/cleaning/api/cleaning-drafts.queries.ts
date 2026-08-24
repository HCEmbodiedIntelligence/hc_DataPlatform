import { useQuery } from '@tanstack/react-query';
import { request } from '../../../shared/api/http-client';
import { useShellStore } from '../../../shared/scope/shell-store';
import type { CleaningDraftsSearch } from '../../../pages/p10-cleaning-drafts/query-codec';
import {
  adaptCleaningDraftDetailEnvelope,
  adaptCleaningDraftEventsEnvelope,
  adaptCleaningDraftListEnvelope,
  adaptCleaningDraftSummaryEnvelope,
} from './cleaning-drafts.adapter';
import { cleaningDraftKeys } from './query-keys';

export const CLEANING_DRAFT_SORT_TO_WIRE = {
  updatedAtDesc: 'updated_at:desc,id:desc',
  updatedAtAsc: 'updated_at:asc,id:asc',
  createdAtDesc: 'created_at:desc,id:desc',
  reuseRatioDesc: 'reuse_ratio:desc,updated_at:desc,id:desc',
  effectiveDurationDesc: 'estimated_effective_duration_ns:desc,updated_at:desc,id:desc',
} as const;

function useScope() {
  const scope = useShellStore((state) => state.scope);
  return {
    organizationId: scope?.organizationId ?? null,
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

function root(projectId: string, regionCode: string) {
  return `/projects/${encodeURIComponent(projectId)}/regions/${encodeURIComponent(regionCode)}/cleaning-drafts`;
}

function normalized(search: CleaningDraftsSearch): Readonly<Record<string, unknown>> {
  return {
    scope: search.scope, status: search.status, q: search.q,
    datasetId: search.datasetId, baseVersionId: search.baseVersionId, episodeId: search.episodeId,
    robotId: search.robotId, creatorId: search.creatorId,
    updatedFrom: search.updatedFrom, updatedTo: search.updatedTo,
    previewStatus: search.previewStatus, commitStatus: search.commitStatus,
    versionReviewStatus: search.versionReviewStatus, findingType: search.findingType,
    findingSeverity: search.findingSeverity, sort: search.sort, limit: search.limit,
  };
}

export function useCleaningDrafts(search: CleaningDraftsSearch, allowed = true) {
  const scope = useScope();
  const filters = normalized(search);
  return useQuery({
    queryKey: cleaningDraftKeys.list(filters, search.after ?? search.before),
    enabled: allowed && Boolean(scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: search.status === 'active' ? 10_000 : 60_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET', path: root(scope.projectId!, scope.regionCode!),
        query: {
          scope: search.scope, status: search.status, q: search.q,
          datasetId: search.datasetId, baseVersionId: search.baseVersionId, episodeId: search.episodeId,
          robotId: search.robotId, creatorId: search.creatorId,
          updatedFrom: search.updatedFrom, updatedTo: search.updatedTo,
          previewStatus: search.previewStatus, commitStatus: search.commitStatus,
          versionReviewStatus: search.versionReviewStatus,
          findingType: search.findingType, findingSeverity: search.findingSeverity,
          sort: CLEANING_DRAFT_SORT_TO_WIRE[search.sort], after: search.after, before: search.before, limit: search.limit,
        }, signal,
      });
      return adaptCleaningDraftListEnvelope(raw, {
        organizationId: scope.organizationId!, projectId: scope.projectId!, regionCode: scope.regionCode!,
      });
    },
  });
}

export function useCleaningDraftSummary(search: CleaningDraftsSearch, allowed = true) {
  const scope = useScope();
  const filters = normalized(search);
  return useQuery({
    queryKey: cleaningDraftKeys.summary(filters),
    enabled: allowed && Boolean(scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET', path: `${root(scope.projectId!, scope.regionCode!)}:summary`,
        query: {
          scope: search.scope, status: search.status, q: search.q, datasetId: search.datasetId,
          baseVersionId: search.baseVersionId, episodeId: search.episodeId,
          robotId: search.robotId, creatorId: search.creatorId,
          updatedFrom: search.updatedFrom, updatedTo: search.updatedTo,
          previewStatus: search.previewStatus, commitStatus: search.commitStatus,
          versionReviewStatus: search.versionReviewStatus,
          findingType: search.findingType, findingSeverity: search.findingSeverity,
        }, signal,
      });
      return adaptCleaningDraftSummaryEnvelope(raw, {
        organizationId: scope.organizationId!, projectId: scope.projectId!, regionCode: scope.regionCode!,
      });
    },
  });
}

export function useCleaningDraftDetail(draftId: string | undefined, allowed = true) {
  const scope = useScope();
  return useQuery({
    queryKey: cleaningDraftKeys.detail(draftId ?? 'invalid'),
    enabled: allowed && Boolean(draftId && scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET', path: `${root(scope.projectId!, scope.regionCode!)}/${encodeURIComponent(draftId!)}/summary`, signal,
      });
      return adaptCleaningDraftDetailEnvelope(raw, {
        organizationId: scope.organizationId!, projectId: scope.projectId!, regionCode: scope.regionCode!,
      }, draftId!);
    },
  });
}

export function useCleaningDraftEvents(draftId: string | undefined, allowed = true) {
  const scope = useScope();
  return useQuery({
    queryKey: cleaningDraftKeys.events(draftId ?? 'invalid', 10),
    enabled: allowed && Boolean(draftId && scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: 'GET',
        path: `${root(scope.projectId!, scope.regionCode!)}/${encodeURIComponent(draftId!)}/events`,
        query: { limit: 10 },
        signal,
      });
      return adaptCleaningDraftEventsEnvelope(raw, {
        organizationId: scope.organizationId!,
        projectId: scope.projectId!,
        regionCode: scope.regionCode!,
      });
    },
  });
}
