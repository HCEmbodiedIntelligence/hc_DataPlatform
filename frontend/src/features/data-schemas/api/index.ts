import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import type { DataSchemaVersion } from '../../../entities/data-schema';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';
import { projectCompatibilityResult } from '../registry-rules';
import type { DataSchemaRouteParams } from '../routing';

const id = z.string().min(1).max(128);
const version = z.string().regex(/^[1-9]\d*$/u);
const blockedReasonSchema = z.object({ code: z.string(), message: z.string() }).strict();
const scopeSchema = z.object({ organization_id: id }).strict();
export const dataSchemaVersionWireSchema = z.object({
  schema_id: id,
  family_id: id,
  schema_version: version,
  display_name: z.string(),
  logical_type: z.string(),
  status: z.string(),
  compatibility_mode: z.string(),
  compatibility_result: z.string().nullable(),
  schema_hash: z.object({ algorithm: z.string(), canonicalization_version: z.string(), value: z.string() }).nullable(),
  schema_definition: z.record(z.string(), z.unknown()),
  etag: z.string(),
  allowed_actions: z.array(z.string()),
  blocked_reasons: z.array(blockedReasonSchema),
}).strict();
const pageInfoSchema = z.object({ has_next_page: z.boolean(), has_previous_page: z.boolean(), start_cursor: z.string().nullable(), end_cursor: z.string().nullable() }).strict();
export const dataSchemasPageWireSchema = z.object({
  items: z.array(dataSchemaVersionWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();
export const dataSchemaEnvelopeWireSchema = z.object({
  data: dataSchemaVersionWireSchema, scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();

const statuses = ['DRAFT', 'VALIDATING', 'PUBLISHED'] as const;
const modes = ['STRICT', 'BACKWARD', 'FORWARD', 'FULL', 'MANUAL'] as const;

export function adaptDataSchemaVersion(wire: z.infer<typeof dataSchemaVersionWireSchema>): DataSchemaVersion {
  return {
    schemaId: wire.schema_id,
    familyId: wire.family_id,
    version: wire.schema_version as `${bigint}`,
    displayName: wire.display_name,
    logicalType: wire.logical_type,
    status: statuses.includes(wire.status as (typeof statuses)[number]) ? wire.status as (typeof statuses)[number] : 'UNKNOWN',
    compatibilityMode: modes.includes(wire.compatibility_mode as (typeof modes)[number]) ? wire.compatibility_mode as (typeof modes)[number] : 'UNKNOWN',
    compatibilityResult: wire.compatibility_result === null ? null : projectCompatibilityResult(wire.compatibility_result),
    hash: wire.schema_hash ? { algorithm: wire.schema_hash.algorithm === 'SHA-256' ? 'SHA-256' : 'UNKNOWN', canonicalizationVersion: wire.schema_hash.canonicalization_version, value: wire.schema_hash.value } : null,
    definition: wire.schema_definition,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useOrganizationId(): string | null {
  return useShellStore((state) => state.scope?.organizationId ?? null);
}

const routeResolutionEnvelopeSchema = z.object({
  data: z.object({
    canonical_query: z.object({ schema_id: id, schema_version: version, component_id: id, detail_tab: z.enum(['fields', 'encoding', 'compatibility', 'references']) }).strict(),
    scope: z.object({ organization_id: id, project_id: id.optional(), region_code: z.string().optional() }).strict(),
    relation_revision: z.string().min(1),
    allowed_actions: z.tuple([z.literal('VIEW')]),
    blocked_reasons: z.array(blockedReasonSchema),
  }).strict(),
  scope: z.object({ organization_id: id, project_id: id.optional(), region_code: z.string().optional() }).strict(),
  request_id: id,
  contract_version: z.string(),
}).strict();

export function useResolveDataSchemaRoute(params: DataSchemaRouteParams | null) {
  const organizationId = useOrganizationId();
  const scope = useShellStore((state) => state.scope);
  return useQuery({
    queryKey: makeQueryKey('data-schemas', 'route-resolution', params),
    enabled: Boolean(params && organizationId && scope?.projectId && scope.regionCode),
    retry: false,
    queryFn: async ({ signal }) => {
      if (!params || !scope?.projectId || !scope.regionCode) throw new Error('Data schema route scope is unavailable');
      const raw = await request<unknown>({
        method: 'GET',
        path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/route-resolutions/p15-to-p17`,
        query: { schemaId: params.schemaId, schemaVersion: params.schemaVersion, componentId: params.componentId, detailTab: params.detailTab },
        signal,
      });
      return parseWire(routeResolutionEnvelopeSchema, raw, { endpoint: 'resolveP15DataSchemaRoute' }).data;
    },
  });
}

export function useDataSchemas(filters: Readonly<Record<string, string>> = {}) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey('data-schemas', 'list', filters), enabled: Boolean(organizationId), staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/stream-schemas`, query: filters, signal });
      const page = parseWire(dataSchemasPageWireSchema, raw, { endpoint: 'dataSchemaListStreamSchemas' });
      return { items: page.items.map(adaptDataSchemaVersion), pageInfo: page.page_info, snapshotAt: page.snapshot_at };
    },
  });
}

export function useDataSchemaVersion(schemaId: string | null, schemaVersion: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey('data-schemas', 'version', { schemaId, schemaVersion }), enabled: Boolean(organizationId && schemaId && schemaVersion),
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/stream-schemas/${encodeURIComponent(schemaId ?? '')}/versions/${encodeURIComponent(schemaVersion ?? '')}`, signal });
      return adaptDataSchemaVersion(parseWire(dataSchemaEnvelopeWireSchema, raw, { endpoint: 'dataSchemaGetVersion' }).data);
    },
  });
}

export interface DataSchemaPublishIntent { readonly schemaId: string; readonly schemaVersion: string; readonly etag: string; readonly idempotencyKey: string; readonly preflightToken: string }

const preflightWireSchema = z.object({
  data: z.object({
    allowed: z.boolean(), preflight_token: z.string().min(1).nullable(), expires_at: z.string().datetime({ offset: true }).nullable(), resource_revision: z.string(),
    impacts: z.array(z.object({ code: z.string(), message: z.string() }).strict()), warnings: z.array(blockedReasonSchema), blockers: z.array(blockedReasonSchema),
  }).strict(), scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();

export interface DataSchemaPreflightIntent {
  readonly schemaId: string; readonly schemaVersion: string; readonly etag: string; readonly expectedHash: string;
  readonly validationReportId: string; readonly compatibilityCheckId: string; readonly changeSummary: string; readonly idempotencyKey: string;
}

export function usePreflightDataSchemaPublish() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: async (intent: DataSchemaPreflightIntent) => {
      const raw = await request<unknown>({
        method: 'POST', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/stream-schemas/${encodeURIComponent(intent.schemaId)}/versions/${encodeURIComponent(intent.schemaVersion)}:preflight-publish`,
        body: { expected_hash: intent.expectedHash, expected_etag: intent.etag, validation_report_id: intent.validationReportId, compatibility_check_id: intent.compatibilityCheckId, change_summary: intent.changeSummary, acknowledge_warning_codes: [] },
        ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
      });
      return parseWire(preflightWireSchema, raw, { endpoint: 'dataSchemaPreflightPublish' }).data;
    },
    gcTime: 0,
  });
}

export function usePublishDataSchema() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: DataSchemaPublishIntent) => request<unknown>({
      method: 'POST', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/stream-schemas/${encodeURIComponent(intent.schemaId)}/versions/${encodeURIComponent(intent.schemaVersion)}:publish`, body: { preflight_token: intent.preflightToken }, ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
    }),
    onSuccess: (_data, intent) => {
      void client.invalidateQueries({ queryKey: makeQueryKey('data-schemas', 'version', { schemaId: intent.schemaId, schemaVersion: intent.schemaVersion }) });
      void client.invalidateQueries({ queryKey: makeQueryKey('data-schemas', 'list', {}) });
    },
  });
}
