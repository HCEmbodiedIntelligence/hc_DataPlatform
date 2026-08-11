import { useQuery } from '@tanstack/react-query';
import type { AuditSearch } from '../routing';
import type { AuditScope } from '../types';
import { getAuditBootstrap, getAuditEvent, getAuditFacets, listAuditEvents } from './client';
import { auditQueryKeys } from './query-keys';

export function useAuditBootstrap(scope: AuditScope | null, search: AuditSearch, enabled: boolean) {
  return useQuery({ queryKey: scope ? auditQueryKeys.bootstrap(scope, search) : ['audit', 'disabled', 'bootstrap'], queryFn: ({ signal }) => getAuditBootstrap(scope!, search, signal), enabled: enabled && scope !== null, staleTime: 30_000, retry: 2 });
}

export function useAuditFacets(scope: AuditScope | null, search: AuditSearch, enabled: boolean) {
  return useQuery({ queryKey: scope ? auditQueryKeys.facets(scope, search) : ['audit', 'disabled', 'facets'], queryFn: ({ signal }) => getAuditFacets(scope!, search, signal), enabled: enabled && scope !== null, staleTime: 30_000, retry: 2 });
}

export function useAuditEvents(scope: AuditScope | null, search: AuditSearch, enabled: boolean) {
  return useQuery({ queryKey: scope ? auditQueryKeys.events(scope, search) : ['audit', 'disabled', 'events'], queryFn: ({ signal }) => listAuditEvents(scope!, search, signal), enabled: enabled && scope !== null, staleTime: 30_000, retry: 2 });
}

export function useAuditEvent(scope: AuditScope | null, eventId: string | undefined, enabled: boolean) {
  return useQuery({ queryKey: scope && eventId ? auditQueryKeys.event(scope, eventId) : ['audit', 'disabled', 'event'], queryFn: ({ signal }) => getAuditEvent(scope!, eventId!, signal), enabled: enabled && scope !== null && eventId !== undefined, staleTime: 300_000, retry: false });
}
