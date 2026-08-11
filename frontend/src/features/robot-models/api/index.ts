import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import type { RobotModel, RobotModelVersion } from '../../../entities/robot-model';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';

const id = z.string().min(1).max(128);
const blockedReasonSchema = z.object({ code: z.string(), message: z.string() }).strict();
const scopeSchema = z.object({ organization_id: id, project_id: id.optional() }).strict();
const modelWireSchema = z.object({
  id,
  manufacturer: z.string(),
  model_code: z.string(),
  display_name: z.string(),
  current_published_version_id: id.nullable(),
}).strict();
const versionWireSchema = z.object({
  id,
  robot_model_id: id,
  version_label: z.string(),
  lifecycle: z.string(),
  asset_availability: z.string(),
  publish_readiness: z.string(),
  asset_manifest_hash: z.string().nullable(),
  validation_input_hash: z.string().nullable(),
  etag: z.string(),
  allowed_actions: z.array(z.string()),
  blocked_reasons: z.array(blockedReasonSchema),
}).strict();
const pageInfoSchema = z.object({
  has_next_page: z.boolean(), has_previous_page: z.boolean(), start_cursor: z.string().nullable(), end_cursor: z.string().nullable(),
}).strict();

export const robotModelsPageWireSchema = z.object({
  items: z.array(modelWireSchema),
  page_info: pageInfoSchema,
  snapshot_at: z.string().datetime({ offset: true }),
  scope: scopeSchema,
  request_id: id,
  contract_version: z.string(),
}).strict();
export const robotModelVersionEnvelopeWireSchema = z.object({
  data: versionWireSchema,
  scope: scopeSchema,
  request_id: id,
  contract_version: z.string(),
}).strict();

const lifecycles = ['DRAFT', 'PUBLISHED', 'DISABLED'] as const;
const availability = ['UNKNOWN', 'AVAILABLE', 'PARTIAL', 'MISSING'] as const;
const readiness = ['CONFIGURATION_REQUIRED', 'MAPPING_REQUIRED', 'SAMPLE_VALIDATION_REQUIRED', 'READY', 'BLOCKED'] as const;

export function adaptRobotModel(wire: z.infer<typeof modelWireSchema>): RobotModel {
  return { id: wire.id, manufacturer: wire.manufacturer, modelCode: wire.model_code, displayName: wire.display_name, currentPublishedVersionId: wire.current_published_version_id };
}

export function adaptRobotModelVersion(wire: z.infer<typeof versionWireSchema>): RobotModelVersion {
  return {
    id: wire.id,
    robotModelId: wire.robot_model_id,
    versionLabel: wire.version_label,
    lifecycle: lifecycles.includes(wire.lifecycle as (typeof lifecycles)[number]) ? wire.lifecycle as (typeof lifecycles)[number] : 'UNKNOWN',
    assetAvailability: availability.includes(wire.asset_availability as (typeof availability)[number]) ? wire.asset_availability as (typeof availability)[number] : 'UNKNOWN',
    publishReadiness: readiness.includes(wire.publish_readiness as (typeof readiness)[number]) ? wire.publish_readiness as (typeof readiness)[number] : 'UNKNOWN',
    assetManifestHash: wire.asset_manifest_hash,
    validationInputHash: wire.validation_input_hash,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useOrganizationId(): string | null {
  return useShellStore((state) => state.scope?.organizationId ?? null);
}

export function useRobotModels(filters: Readonly<Record<string, unknown>> = {}) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey('robot-models', 'list', filters),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/robot-models`, query: filters as Readonly<Record<string, string>>, signal });
      const page = parseWire(robotModelsPageWireSchema, raw, { endpoint: 'listRobotModels' });
      return { items: page.items.map(adaptRobotModel), pageInfo: page.page_info, snapshotAt: page.snapshot_at, requestId: page.request_id };
    },
  });
}

export function useRobotModelVersion(versionId: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey('robot-models', 'version', versionId),
    enabled: Boolean(organizationId && versionId),
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/organizations/${encodeURIComponent(organizationId ?? '')}/robot-model-versions/${encodeURIComponent(versionId ?? '')}`, signal });
      return adaptRobotModelVersion(parseWire(robotModelVersionEnvelopeWireSchema, raw, { endpoint: 'getRobotModelVersion' }).data);
    },
  });
}

export interface RobotModelDangerousIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

const preflightWireSchema = z.object({
  data: z.object({
    allowed: z.boolean(),
    preflight_token: z.string().min(1).nullable(),
    expires_at: z.string().datetime({ offset: true }).nullable(),
    resource_revision: z.string().min(1),
    impacts: z.array(z.object({ code: z.string(), message: z.string() }).strict()),
    warnings: z.array(blockedReasonSchema),
    blockers: z.array(blockedReasonSchema),
  }).strict(),
  scope: scopeSchema,
  request_id: id,
  contract_version: z.string().min(1),
}).strict();

export interface RobotModelPublishPreflightIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly expectedHash: string;
  readonly validationReportId: string;
  readonly changeSummary: string;
  readonly idempotencyKey: string;
}

export function usePreflightRobotModelPublish() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: async (intent: RobotModelPublishPreflightIntent) => {
      const raw = await request<unknown>({
        method: 'POST',
        path: `/organizations/${encodeURIComponent(organizationId ?? '')}/robot-model-versions/${encodeURIComponent(intent.versionId)}:preflight-publish`,
        body: { expected_hash: intent.expectedHash, expected_etag: intent.etag, validation_report_id: intent.validationReportId, compatibility_check_id: null, change_summary: intent.changeSummary, acknowledge_warning_codes: [] },
        ifMatch: intent.etag,
        idempotencyKey: intent.idempotencyKey,
      });
      return parseWire(preflightWireSchema, raw, { endpoint: 'preflightRobotModelPublish' }).data;
    },
    gcTime: 0,
  });
}

export function usePublishRobotModelVersion() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: RobotModelDangerousIntent) => request<unknown>({
      method: 'POST',
      path: `/organizations/${encodeURIComponent(organizationId ?? '')}/robot-model-versions/${encodeURIComponent(intent.versionId)}:publish`,
      body: { preflight_token: intent.preflightToken },
      idempotencyKey: intent.idempotencyKey,
      ifMatch: intent.etag,
    }),
    onSuccess: (_data, intent) => {
      void client.invalidateQueries({ queryKey: makeQueryKey('robot-models', 'version', intent.versionId) });
      void client.invalidateQueries({ queryKey: makeQueryKey('robot-models', 'list', {}) });
    },
  });
}

export interface UploadSessionIntent {
  readonly versionId: string;
  readonly files: readonly { readonly relative_path: string; readonly size_bytes: string; readonly sha256: string }[];
  readonly idempotencyKey: string;
}

/** Ephemeral grants are returned directly to the caller and never enter Query Cache. */
export function useCreateRobotAssetUploadSession() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: (intent: UploadSessionIntent) => request<unknown>({
      method: 'POST',
      path: `/organizations/${encodeURIComponent(organizationId ?? '')}/robot-model-versions/${encodeURIComponent(intent.versionId)}/upload-sessions`,
      body: { files: intent.files },
      idempotencyKey: intent.idempotencyKey,
    }),
    gcTime: 0,
  });
}
