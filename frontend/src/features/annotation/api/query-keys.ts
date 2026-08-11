import { makeQueryKey } from '../../../shared/api/query-keys';
import type { AnnotationEntryContext, AnnotationListFilters, AnnotationScope } from './client';

function normalizedFilters(filters: AnnotationListFilters): Readonly<Record<string, unknown>> {
  return {
    queue: filters.queue,
    states: filters.states ? [...filters.states].sort() : [],
    datasetId: filters.datasetId ?? null,
    schemaVersionId: filters.schemaVersionId ?? null,
    assigneeId: filters.assigneeId ?? null,
    q: filters.q ?? null,
    sort: filters.sort ?? 'priority_desc',
    after: filters.after ?? null,
    before: filters.before ?? null,
    limit: filters.limit,
  };
}

export const annotationQueryKeys = {
  lists: (scope: AnnotationScope) => makeQueryKey('annotation', 'tasks', { projectId: scope.projectId, regionCode: scope.regionCode, kind: 'list' }),
  list: (scope: AnnotationScope, filters: AnnotationListFilters) => makeQueryKey('annotation', 'tasks', { projectId: scope.projectId, regionCode: scope.regionCode, ...normalizedFilters(filters) }),
  task: (scope: AnnotationScope, taskId: string) => makeQueryKey('annotation', 'task-bootstrap', { projectId: scope.projectId, regionCode: scope.regionCode, taskId }),
  draft: (scope: AnnotationScope, taskId: string, revision: number) => makeQueryKey('annotation', 'draft-revision', { projectId: scope.projectId, regionCode: scope.regionCode, taskId, revision }),
  issueProjection: (scope: AnnotationScope, taskId: string) => makeQueryKey('annotation', 'manual-issue-projection', { projectId: scope.projectId, regionCode: scope.regionCode, taskId }),
  entryResolution: (context: AnnotationEntryContext) => makeQueryKey('annotation', 'entry-resolution', context),
} as const;
