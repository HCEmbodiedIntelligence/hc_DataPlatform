import { makeQueryKey, normalizeFilters } from '../../../shared/api/query-keys';
import type { StorageOverviewSearch } from '../routing';
import type { StorageScope } from '../types';

export const storageOverviewQueryKeys = {
  overview: (scope: StorageScope, months: number) => makeQueryKey('storage', 'overview', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, months }),
  inventory: (scope: StorageScope, search: StorageOverviewSearch) => makeQueryKey('storage', 'objects', normalizeFilters({
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    objectRole: search.objectRole,
    storageClass: search.storageClass,
    anomaly: search.anomaly,
    status: search.status,
    sort: search.sort,
    after: search.after,
    before: search.before,
    limit: search.limit,
  }, { limit: 50, sort: 'physicalBytes:desc,objectId:desc' })),
  object: (scope: StorageScope, objectId: string, snapshotId: string) => makeQueryKey('storage', 'object', {
    organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, objectId,
  }, snapshotId),
  multipart: (scope: StorageScope, search: StorageOverviewSearch) => makeQueryKey('storage', 'multipart', normalizeFilters({
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    status: search.status,
    after: search.after,
    before: search.before,
    limit: search.limit,
    sort: 'lastActivityAt:asc,multipartId:asc',
  }, { status: [], limit: 50 })),
  cost: (scope: StorageScope, billingPeriod: string | undefined) => makeQueryKey('storage', 'cost', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, billingPeriod }),
} as const;
