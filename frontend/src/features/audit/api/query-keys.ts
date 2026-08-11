import { makeQueryKey, normalizeFilters } from '../../../shared/api/query-keys';
import type { AuditSearch } from '../routing';
import type { AuditScope } from '../types';

function normalized(search: AuditSearch) {
  return normalizeFilters({
    from: search.from,
    to: search.to,
    actorId: search.actorId,
    eventName: search.eventName,
    resourceType: search.resourceType,
    resourceId: search.resourceId,
    result: search.result,
    riskLevel: search.riskLevel,
    requestId: search.requestId,
    sort: search.sort,
    after: search.after,
    before: search.before,
    limit: search.limit,
  }, { actorId: [], eventName: [], resourceType: [], result: [], riskLevel: [], sort: 'occurredAt:desc', limit: 50 });
}

export const auditQueryKeys = {
  bootstrap: (scope: AuditScope, search: AuditSearch) => makeQueryKey('audit', 'bootstrap', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, from: search.from, to: search.to }),
  facets: (scope: AuditScope, search: AuditSearch) => makeQueryKey('audit', 'facets', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, ...normalized(search), after: undefined, before: undefined }),
  events: (scope: AuditScope, search: AuditSearch) => makeQueryKey('audit', 'events', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, ...normalized(search) }),
  event: (scope: AuditScope, eventId: string) => makeQueryKey('audit', 'event', { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, eventId }),
} as const;
