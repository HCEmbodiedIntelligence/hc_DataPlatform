import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { IngestScope } from '../../../entities/data-source';
import { isUploadTerminal, type UploadBootstrap, type UploadSession } from '../upload/model';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { adaptDataSource, adaptDataSourcePage, adaptRawObject, adaptScope, adaptUploadBootstrap, adaptUploadSession, adaptVerificationRun, assertResponseScope } from './adapters';
import {
  getDataSource,
  getDataSourcesPage,
  getUploadSessionBootstrap,
  getUploadCreationOptions,
  listUploadObjects,
  listUploadEvents,
  listUploadSessions,
  listVerificationRuns,
} from './client';

export interface DataSourceFilters {
  readonly q?: string;
  readonly sourceType?: readonly string[];
  readonly administrativeState?: readonly string[];
  readonly connectivity?: readonly string[];
  readonly credentialState?: readonly string[];
  readonly sort: 'updatedAt:desc' | 'name:asc' | 'lastTestAt:desc';
  readonly after?: string;
  readonly before?: string;
  readonly limit: 10 | 20 | 50;
}

export interface UploadFilters {
  readonly q?: string;
  readonly dataSourceId?: string;
  readonly datasetId?: string;
  readonly lifecycleStatus?: readonly string[];
  readonly verificationStatus?: readonly string[];
  readonly sort: 'createdAt:desc' | 'updatedAt:desc';
  readonly after?: string;
  readonly before?: string;
  readonly limit: 10 | 20 | 50;
}

export interface UploadSessionPage {
  readonly items: readonly UploadSession[];
  readonly pageInfo: { readonly hasNextPage: boolean; readonly hasPreviousPage: boolean; readonly startCursor: string | null; readonly endCursor: string | null };
  readonly snapshotAt: string;
  readonly requestId: string;
}

export function useDataSourcesPage(scope: IngestScope | null, filters: DataSourceFilters, enabled = true) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'data-source-page', filters),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error('INGEST_SCOPE_UNAVAILABLE');
      const sort = filters.sort === 'name:asc'
        ? 'name:asc,id:asc'
        : filters.sort === 'lastTestAt:desc'
          ? 'last_test_at:desc,id:desc'
          : 'updated_at:desc,id:desc';
      return adaptDataSourcePage(await getDataSourcesPage(scope, {
        q: filters.q,
        sourceType: filters.sourceType,
        administrativeState: filters.administrativeState,
        connectivityState: filters.connectivity,
        credentialState: filters.credentialState,
        sort,
        after: filters.after,
        before: filters.before,
        limit: filters.limit,
      }, signal), scope);
    },
  });
}

export function useDataSource(scope: IngestScope | null, sourceId: string | null, enabled = true) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'data-source', sourceId),
    enabled: enabled && scope !== null && Boolean(sourceId),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      if (!scope || !sourceId) throw new Error('DATA_SOURCE_ID_UNAVAILABLE');
      return adaptDataSource((await getDataSource(scope, sourceId, signal)).data, scope);
    },
  });
}

export function useUploadSessions(scope: IngestScope | null, filters: UploadFilters, enabled = true) {
  return useQuery<UploadSessionPage>({
    queryKey: makeQueryKey('ingest', 'upload-sessions', filters),
    enabled: enabled && scope !== null,
    staleTime: 15_000,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => query.state.data?.items.some((item) => !isUploadTerminal(item.lifecycleStatus)) ? 5_000 : 30_000,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error('INGEST_SCOPE_UNAVAILABLE');
      const wire = await listUploadSessions(scope, {
        ...filters,
        sort: filters.sort === 'updatedAt:desc' ? 'updated_at:desc,upload_id:desc' : 'created_at:desc,upload_id:desc',
      }, signal);
      assertResponseScope(adaptScope(wire.scope), scope);
      return {
        items: wire.items.map((item) => adaptUploadSession(item, scope)),
        pageInfo: {
          hasNextPage: wire.page_info.has_next_page,
          hasPreviousPage: wire.page_info.has_previous_page,
          startCursor: wire.page_info.start_cursor,
          endCursor: wire.page_info.end_cursor,
        },
        snapshotAt: wire.snapshot_at,
        requestId: wire.request_id,
      };
    },
  });
}

export function useUploadCreationOptions(
  scope: IngestScope | null,
  targets: { readonly targetDataSourceId?: string; readonly targetDatasetId?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'upload-creation-options', targets),
    enabled: enabled && scope !== null,
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error('INGEST_SCOPE_UNAVAILABLE');
      const wire = await getUploadCreationOptions(scope, { ...targets }, signal);
      assertResponseScope(adaptScope(wire.scope), scope);
      return {
        dataSources: wire.data.data_sources.map((source) => ({
          id: source.id, name: source.name, sourceType: source.source_type, sourceFormat: source.source_format,
          configurationVersion: source.configuration_version, credentialVersion: source.credential_version,
          uploadPolicyVersion: source.upload_policy_version, allowed: source.allowed, blockedReasons: source.blocked_reasons,
        })),
        datasets: wire.data.datasets.map((dataset) => ({ id: dataset.id, name: dataset.name, allowed: dataset.allowed, blockedReasons: dataset.blocked_reasons })),
        formats: wire.data.formats,
        policySummaries: wire.data.policy_summaries,
        allowedActions: wire.data.allowed_actions,
        blockedReasons: wire.data.blocked_reasons,
      };
    },
  });
}

export function useUploadBootstrap(scope: IngestScope | null, uploadId: string | null, enabled = true) {
  return useQuery<UploadBootstrap>({
    queryKey: makeQueryKey('ingest', 'upload-session', uploadId),
    enabled: enabled && scope !== null && Boolean(uploadId),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const status = query.state.data?.session.lifecycleStatus;
      return typeof status === 'string' && ['AVAILABLE', 'FAILED', 'QUARANTINED', 'CANCELLED', 'EXPIRED'].includes(status)
        ? false
        : 5_000;
    },
    queryFn: async ({ signal }) => {
      if (!scope || !uploadId) throw new Error('UPLOAD_ID_UNAVAILABLE');
      return adaptUploadBootstrap(await getUploadSessionBootstrap(scope, uploadId, signal), scope);
    },
  });
}

export function useUploadObjects(
  scope: IngestScope | null,
  uploadId: string | null,
  filters: { readonly after?: string; readonly before?: string; readonly limit: 50; readonly sort: string },
  enabled = true,
) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'upload-objects', { uploadId, ...filters }),
    enabled: enabled && scope !== null && Boolean(uploadId),
    placeholderData: keepPreviousData,
    queryFn: async ({ signal }) => {
      if (!scope || !uploadId) throw new Error('UPLOAD_ID_UNAVAILABLE');
      const wire = await listUploadObjects(scope, uploadId, {
        ...filters,
        sort: filters.sort === 'relativePath:asc' ? 'relative_path:asc,object_id:asc' : filters.sort,
      }, signal);
      assertResponseScope(adaptScope(wire.scope), scope);
      return {
        items: wire.items.map(adaptRawObject),
        pageInfo: wire.page_info,
        snapshotAt: wire.snapshot_at,
      };
    },
  });
}

export function useVerificationRuns(
  scope: IngestScope | null,
  uploadId: string | null,
  filters: { readonly after?: string; readonly before?: string; readonly limit: 50 },
  enabled = true,
) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'verification-runs', { uploadId, ...filters }),
    enabled: enabled && scope !== null && Boolean(uploadId),
    placeholderData: keepPreviousData,
    queryFn: async ({ signal }) => {
      if (!scope || !uploadId) throw new Error('UPLOAD_ID_UNAVAILABLE');
      const wire = await listVerificationRuns(scope, uploadId, filters, signal);
      assertResponseScope(adaptScope(wire.scope), scope);
      return { items: wire.items.map(adaptVerificationRun), pageInfo: wire.page_info, snapshotAt: wire.snapshot_at };
    },
  });
}

export function useUploadEvents(
  scope: IngestScope | null,
  uploadId: string | null,
  filters: { readonly eventLevel?: readonly string[]; readonly after?: string; readonly before?: string; readonly limit: 50 },
  enabled = true,
) {
  return useQuery({
    queryKey: makeQueryKey('ingest', 'upload-events', { uploadId, ...filters }),
    enabled: enabled && scope !== null && Boolean(uploadId),
    placeholderData: keepPreviousData,
    refetchInterval: enabled ? 5_000 : false,
    queryFn: async ({ signal }) => {
      if (!scope || !uploadId) throw new Error('UPLOAD_ID_UNAVAILABLE');
      const wire = await listUploadEvents(scope, uploadId, { ...filters }, signal);
      assertResponseScope(adaptScope(wire.scope), scope);
      return {
        items: wire.items.map((event) => ({
          eventId: event.event_id,
          eventType: event.event_type,
          eventLevel: event.event_level,
          occurredAt: event.occurred_at,
          actor: { kind: event.actor.kind, id: event.actor.id, displayName: event.actor.display_name },
          fromState: event.from_state,
          toState: event.to_state,
          resourceRef: { type: event.resource_ref.type, id: event.resource_ref.id, version: event.resource_ref.version },
          jobId: event.job_id,
          requestId: event.request_id,
          safePayload: event.safe_payload,
        })),
        pageInfo: wire.page_info,
        snapshotAt: wire.snapshot_at,
      };
    },
  });
}
