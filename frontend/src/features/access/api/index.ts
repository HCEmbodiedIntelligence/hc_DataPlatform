import { useMutation, useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';
import { capabilityCatalog, type ProjectRoleId } from '../capability-catalog';

const id = z.string().min(1).max(128);
const uint64 = z.string().regex(/^(0|[1-9]\d*)$/u);
const projectRoleSchema = z.enum(['PROJECT_ADMIN', 'PROJECT_DEVELOPER', 'PROJECT_DATA_PROCESSOR']);
const blockedReasonSchema = z.object({ code: z.string(), message: z.string() }).strict();
export const accessBootstrapWireSchema = z.object({
  data: z.object({
    role_version: z.string(), capability_catalog_version: z.string(), policy_revision: z.string(), project_policy_etag: z.string(),
    summary: z.object({ active_members: uint64, pending_invitations: uint64, as_of: z.string().datetime({ offset: true }) }).strict(),
    allowed_actions: z.array(z.string()), blocked_reasons: z.array(blockedReasonSchema),
  }).strict(),
  scope: z.object({ project_id: id }).strict(), request_id: id, contract_version: z.string(),
}).strict();
const memberWireSchema = z.object({
  id,
  principal: z.object({ id, display_name: z.string(), secondary_display: z.string().nullable(), identity_status: z.string() }).strict(),
  status: z.string(), role_id: z.string(), role_version: z.string(), joined_at: z.string().datetime({ offset: true }).nullable(), last_active_at: z.string().datetime({ offset: true }).nullable(), etag: z.string(), allowed_actions: z.array(z.string()), blocked_reasons: z.array(blockedReasonSchema),
}).strict();
const pageInfoSchema = z.object({ has_next_page: z.boolean(), has_previous_page: z.boolean(), start_cursor: z.string().nullable(), end_cursor: z.string().nullable() }).strict();
export const membersPageWireSchema = z.object({
  items: z.array(memberWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: z.object({ project_id: id }).strict(), request_id: id, contract_version: z.string(),
}).strict();

export interface AccessBootstrapVm {
  readonly roleVersion: string;
  readonly capabilityCatalogVersion: string;
  readonly policyRevision: string;
  readonly projectPolicyEtag: string;
  readonly activeMembers: bigint;
  readonly pendingInvitations: bigint;
  readonly asOf: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
}

export interface ProjectMemberVm {
  readonly id: string;
  readonly principalId: string;
  readonly displayName: string;
  readonly secondaryDisplay: string | null;
  readonly status: 'ACTIVE' | 'DISABLED' | 'REMOVED' | 'UNKNOWN';
  readonly roleId: ProjectRoleId | 'UNKNOWN';
  readonly roleVersion: string;
  readonly joinedAt: string | null;
  readonly lastActiveAt: string | null;
  readonly etag: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
}

export function adaptAccessBootstrap(wire: z.infer<typeof accessBootstrapWireSchema>): AccessBootstrapVm {
  return {
    roleVersion: wire.data.role_version,
    capabilityCatalogVersion: wire.data.capability_catalog_version,
    policyRevision: wire.data.policy_revision,
    projectPolicyEtag: wire.data.project_policy_etag,
    activeMembers: BigInt(wire.data.summary.active_members),
    pendingInvitations: BigInt(wire.data.summary.pending_invitations),
    asOf: wire.data.summary.as_of,
    allowedActions: wire.data.allowed_actions,
    blockedReasons: wire.data.blocked_reasons,
  };
}

const memberStatuses = ['ACTIVE', 'DISABLED', 'REMOVED'] as const;
export function adaptMember(wire: z.infer<typeof memberWireSchema>): ProjectMemberVm {
  const parsedRole = projectRoleSchema.safeParse(wire.role_id);
  return {
    id: wire.id,
    principalId: wire.principal.id,
    displayName: wire.principal.display_name,
    secondaryDisplay: wire.principal.secondary_display,
    status: memberStatuses.includes(wire.status as (typeof memberStatuses)[number]) ? wire.status as (typeof memberStatuses)[number] : 'UNKNOWN',
    roleId: parsedRole.success ? parsedRole.data : 'UNKNOWN',
    roleVersion: wire.role_version,
    joinedAt: wire.joined_at,
    lastActiveAt: wire.last_active_at,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useProjectId(): string | null {
  return useShellStore((state) => state.scope?.projectId ?? null);
}

export function useAccessBootstrap() {
  const projectId = useProjectId();
  return useQuery({
    queryKey: makeQueryKey('authorization', 'bootstrap', projectId), enabled: Boolean(projectId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/access/bootstrap`, signal });
      return adaptAccessBootstrap(parseWire(accessBootstrapWireSchema, raw, { endpoint: 'getAccessBootstrap' }));
    },
  });
}

export function useProjectMembers(filters: Readonly<Record<string, string>> = {}) {
  const projectId = useProjectId();
  return useQuery({
    queryKey: makeQueryKey('authorization', 'members', filters), enabled: Boolean(projectId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/members`, query: filters, signal });
      const page = parseWire(membersPageWireSchema, raw, { endpoint: 'listProjectMembers' });
      return { items: page.items.map(adaptMember), pageInfo: page.page_info, snapshotAt: page.snapshot_at };
    },
  });
}

export function useRoleCapabilityCatalog() {
  return {
    version: capabilityCatalog.version,
    canonical: capabilityCatalog.canonical,
    reserved: capabilityCatalog.reserved,
    roleCeilings: capabilityCatalog.roleCeilings,
  } as const;
}

export interface AuthorizationConvergence {
  cancelAffectedRequests(): Promise<void>;
  clearInvalidData(): void;
  reevaluateCurrentRoute(): void;
}

export interface CommitAccessChangeIntent {
  readonly projectPolicyEtag: string;
  readonly preflightToken: string;
  readonly idempotencyKey: string;
}

export function useCommitAccessChange(convergence: AuthorizationConvergence | null) {
  const projectId = useProjectId();
  return useMutation({
    mutationFn: (intent: CommitAccessChangeIntent) => request<unknown>({
      method: 'POST', path: `/projects/${encodeURIComponent(projectId ?? '')}/access-changes`, body: { preflight_token: intent.preflightToken }, ifMatch: intent.projectPolicyEtag, idempotencyKey: intent.idempotencyKey,
    }),
    onSuccess: async () => {
      if (!convergence) throw new Error('Shared authorization convergence is unavailable');
      await convergence.cancelAffectedRequests();
      convergence.clearInvalidData();
      convergence.reevaluateCurrentRoute();
    },
  });
}
