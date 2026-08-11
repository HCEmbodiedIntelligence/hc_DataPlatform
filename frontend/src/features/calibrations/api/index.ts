import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import type { CalibrationSet } from '../../../entities/calibration';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';

const id = z.string().min(1).max(128);
const uint64 = z.string().regex(/^(0|[1-9]\d*)$/u);
const blockedReasonSchema = z.object({ code: z.string(), message: z.string() }).strict();
const scopeSchema = z.object({ project_id: id, region_code: z.string() }).strict();
export const calibrationSetWireSchema = z.object({
  id,
  robot_instance_id: id,
  component_id: id.nullable(),
  version: uint64,
  snapshot_status: z.string(),
  availability: z.string().nullable(),
  content_hash: z.string().nullable(),
  validation_context_hash: z.string().nullable(),
  validation: z.object({ status: z.string(), content_hash: z.string(), validation_context_hash: z.string(), report_id: id }).strict().nullable(),
  etag: z.string(),
  allowed_actions: z.array(z.string()),
  blocked_reasons: z.array(blockedReasonSchema),
}).strict();
const pageInfoSchema = z.object({ has_next_page: z.boolean(), has_previous_page: z.boolean(), start_cursor: z.string().nullable(), end_cursor: z.string().nullable() }).strict();
export const calibrationSetsPageWireSchema = z.object({
  items: z.array(calibrationSetWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();
export const calibrationSetEnvelopeWireSchema = z.object({
  data: calibrationSetWireSchema, scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();

const snapshotStatuses = ['DRAFT', 'READY'] as const;
const availabilityStatuses = ['SCHEDULED', 'ACTIVE', 'EXPIRED', 'REVOKED'] as const;

export function adaptCalibrationSet(wire: z.infer<typeof calibrationSetWireSchema>): CalibrationSet {
  return {
    id: wire.id,
    robotId: wire.robot_instance_id,
    componentId: wire.component_id,
    version: wire.version as `${bigint}`,
    snapshotStatus: snapshotStatuses.includes(wire.snapshot_status as (typeof snapshotStatuses)[number]) ? wire.snapshot_status as (typeof snapshotStatuses)[number] : 'UNKNOWN',
    availability: wire.availability === null ? null : availabilityStatuses.includes(wire.availability as (typeof availabilityStatuses)[number]) ? wire.availability as (typeof availabilityStatuses)[number] : 'UNKNOWN',
    contentHash: wire.content_hash,
    validationContextHash: wire.validation_context_hash,
    validation: wire.validation ? {
      status: ['PASSED', 'FAILED', 'STALE'].includes(wire.validation.status) ? wire.validation.status as 'PASSED' | 'FAILED' | 'STALE' : 'UNKNOWN',
      contentHash: wire.validation.content_hash,
      validationContextHash: wire.validation.validation_context_hash,
      reportId: wire.validation.report_id,
    } : null,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useCalibrationScope(): { readonly projectId: string | null; readonly regionCode: string | null } {
  const scope = useShellStore((state) => state.scope);
  return { projectId: scope?.projectId ?? null, regionCode: scope?.regionCode ?? null };
}

export function useCalibrationSets(filters: Readonly<Record<string, string>> = {}) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey('calibrations', 'sets', filters), enabled: Boolean(projectId && regionCode), staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/calibration-sets`, query: filters, signal });
      const page = parseWire(calibrationSetsPageWireSchema, raw, { endpoint: 'calibrationListSets' });
      return { items: page.items.map(adaptCalibrationSet), pageInfo: page.page_info, snapshotAt: page.snapshot_at };
    },
  });
}

export function useCalibrationSet(setId: string | null) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey('calibrations', 'set', setId), enabled: Boolean(projectId && regionCode && setId), staleTime: 10_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/calibration-sets/${encodeURIComponent(setId ?? '')}`, signal });
      return adaptCalibrationSet(parseWire(calibrationSetEnvelopeWireSchema, raw, { endpoint: 'calibrationGetSet' }).data);
    },
  });
}

export interface CalibrationPublishIntent {
  readonly setId: string;
  readonly version: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

const preflightWireSchema = z.object({
  data: z.object({
    allowed: z.boolean(), preflight_token: z.string().min(1).nullable(), expires_at: z.string().datetime({ offset: true }).nullable(), resource_revision: z.string(),
    impacts: z.array(z.object({ code: z.string(), message: z.string() }).strict()), warnings: z.array(blockedReasonSchema), blockers: z.array(blockedReasonSchema),
  }).strict(), scope: scopeSchema, request_id: id, contract_version: z.string(),
}).strict();

export interface CalibrationPreflightIntent {
  readonly setId: string; readonly version: string; readonly etag: string; readonly expectedHash: string;
  readonly validationContextHash: string; readonly validationReportId: string; readonly changeSummary: string; readonly idempotencyKey: string;
}

export function usePreflightCalibrationPublish() {
  const { projectId, regionCode } = useCalibrationScope();
  return useMutation({
    mutationFn: async (intent: CalibrationPreflightIntent) => {
      const raw = await request<unknown>({
        method: 'POST', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/calibration-sets/${encodeURIComponent(intent.setId)}/versions/${encodeURIComponent(intent.version)}:preflight-publish`,
        body: { expected_hash: intent.expectedHash, expected_etag: intent.etag, validation_report_id: intent.validationReportId, compatibility_check_id: null, change_summary: intent.changeSummary, acknowledge_warning_codes: [], validation_context_hash: intent.validationContextHash },
        ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
      });
      return parseWire(preflightWireSchema, raw, { endpoint: 'calibrationPreflightPublish' }).data;
    },
    gcTime: 0,
  });
}

export function usePublishCalibration() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CalibrationPublishIntent) => request<unknown>({
      method: 'POST',
      path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/calibration-sets/${encodeURIComponent(intent.setId)}/versions/${encodeURIComponent(intent.version)}:publish`,
      body: { preflight_token: intent.preflightToken }, ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
    }),
    onSuccess: (_data, intent) => {
      void client.invalidateQueries({ queryKey: makeQueryKey('calibrations', 'set', intent.setId) });
      void client.invalidateQueries({ queryKey: makeQueryKey('calibrations', 'sets', {}) });
    },
  });
}
