import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { createDomainError, isDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import { useShellStore } from "../../shared/scope/shell-store";

export type AccountAccessRequestKind = "ORGANIZATION" | "PROJECT" | "CAPABILITY";
export type AccountAccessRequestStatus =
  | "PENDING"
  | "APPROVED"
  | "REJECTED"
  | "WITHDRAWN"
  | "REVOKED";

export interface AccountOrganizationMembership {
  readonly organization_id: string;
  readonly organization_name: string;
  readonly member_status: "ACTIVE";
}

export interface AccountProjectMembership {
  readonly organization_id: string;
  readonly organization_name: string;
  readonly project_id: string;
  readonly project_name: string;
  readonly member_status: "ACTIVE";
}

export interface AccountAccessRequest {
  readonly request_id: string;
  readonly kind: AccountAccessRequestKind;
  readonly organization_id: string;
  readonly project_id: string | null;
  readonly capability_keys: readonly string[];
  readonly status: AccountAccessRequestStatus;
  readonly reason: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly revision?: number;
}

export interface AccountAccessOverview {
  readonly organizations: readonly AccountOrganizationMembership[];
  readonly projects: readonly AccountProjectMembership[];
  readonly requests: readonly AccountAccessRequest[];
  readonly pending_request_count: number;
}

const instant = z.string().datetime({ offset: true });
const organizationSchema: z.ZodType<AccountOrganizationMembership> = z
  .object({
    organization_id: z.string().min(1).max(256),
    organization_name: z.string().min(1).max(256),
    member_status: z.literal("ACTIVE"),
  })
  .strict();
const projectSchema: z.ZodType<AccountProjectMembership> = z
  .object({
    organization_id: z.string().min(1).max(256),
    organization_name: z.string().min(1).max(256),
    project_id: z.string().min(1).max(256),
    project_name: z.string().min(1).max(256),
    member_status: z.literal("ACTIVE"),
  })
  .strict();
const accessRequestSchema: z.ZodType<AccountAccessRequest> = z
  .object({
    request_id: z.string().min(1),
    kind: z.enum(["ORGANIZATION", "PROJECT", "CAPABILITY"]),
    organization_id: z.string().min(1).max(256),
    project_id: z.string().min(1).max(256).nullable(),
    capability_keys: z.array(z.string().min(1)).default([]),
    status: z.enum(["PENDING", "APPROVED", "REJECTED", "WITHDRAWN", "REVOKED"]),
    reason: z.string().nullable().default(null),
    created_at: instant,
    updated_at: instant,
    revision: z.number().int().positive().optional(),
  })
  .strict();
const overviewSchema: z.ZodType<AccountAccessOverview> = z
  .object({
    organizations: z.array(organizationSchema),
    projects: z.array(projectSchema),
    requests: z.array(accessRequestSchema),
    pending_request_count: z.number().int().nonnegative(),
  })
  .strict();

function requestMismatch(message: string): Error {
  return createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export const accountAccessKeys = {
  overview: (principalId: string) => ["account-access", principalId, "overview"] as const,
};

export async function getAccountAccessOverview(signal?: AbortSignal) {
  const endpoint = "/account/access-overview";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(overviewSchema, raw, { endpoint });
}

export async function requestOrganizationMembership(
  organizationIdOrJoinCode: string,
  reason: string,
  idempotencyKey: string,
): Promise<AccountAccessRequest> {
  const endpoint = "/account/organization-membership-requests";
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    idempotencyKey,
    cache: "no-store",
    body: {
      organization_id_or_join_code: organizationIdOrJoinCode,
      reason,
    },
  });
  const wire = raw as Record<string, unknown>;
  const result = parseWire(accessRequestSchema, {
    request_id: wire.request_id,
    kind: wire.kind ?? "ORGANIZATION",
    organization_id: wire.organization_id,
    project_id: null,
    capability_keys: [],
    status: wire.status,
    reason: wire.reason ?? reason,
    created_at: wire.created_at,
    updated_at: wire.updated_at,
    revision: wire.revision,
  }, { endpoint });
  if (result.kind !== "ORGANIZATION") {
    throw requestMismatch("加入组织申请响应类型不正确。");
  }
  return result;
}

export async function requestProjectMembership(
  organizationId: string,
  projectId: string,
  reason: string | null,
  idempotencyKey: string,
): Promise<AccountAccessRequest> {
  const endpoint = `/organizations/${encodeURIComponent(organizationId)}/projects/${encodeURIComponent(projectId)}/membership-requests`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body: { reason },
    idempotencyKey,
    cache: "no-store",
    scopeMode: "session",
  });
  const result = raw as Record<string, unknown>;
  return parseWire(accessRequestSchema, {
    request_id: result.request_id,
    kind: "PROJECT",
    organization_id: result.organization_id ?? organizationId,
    status: result.status,
    reason: result.reason ?? null,
    created_at: result.created_at,
    updated_at: result.updated_at,
    revision: result.revision,
    capability_keys: [],
    project_id: result.project_id ?? projectId,
  }, { endpoint });
}

export async function requestProjectCapabilities(
  organizationId: string,
  projectId: string,
  capabilityKeys: readonly string[],
  reason: string | null,
  idempotencyKey: string,
): Promise<AccountAccessRequest> {
  const endpoint = `/organizations/${encodeURIComponent(organizationId)}/projects/${encodeURIComponent(projectId)}/capability-requests`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body: { capability_keys: [...capabilityKeys], reason },
    idempotencyKey,
    cache: "no-store",
    scopeMode: "session",
  });
  const result = raw as Record<string, unknown>;
  return parseWire(accessRequestSchema, {
    request_id: result.request_id,
    kind: "CAPABILITY",
    organization_id: result.organization_id ?? organizationId,
    status: result.status,
    reason: result.reason ?? null,
    created_at: result.created_at,
    updated_at: result.updated_at,
    revision: result.revision,
    capability_keys: result.capability_keys,
    project_id: result.project_id ?? projectId,
  }, { endpoint });
}

export async function withdrawAccountAccessRequest(
  item: AccountAccessRequest,
): Promise<AccountAccessRequest> {
  const endpoint =
    item.kind === "ORGANIZATION"
      ? `/account/organization-membership-requests/${encodeURIComponent(item.request_id)}:withdraw`
      : `/organizations/${encodeURIComponent(item.organization_id)}/projects/${encodeURIComponent(item.project_id ?? "")}/${item.kind === "PROJECT" ? "membership" : "capability"}-requests/${encodeURIComponent(item.request_id)}:withdraw`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    idempotencyKey: globalThis.crypto.randomUUID(),
    cache: "no-store",
    ...(item.kind === "ORGANIZATION" ? {} : { body: { reason: null } }),
  });
  const result = raw as Record<string, unknown>;
  return parseWire(accessRequestSchema, {
    request_id: result.request_id,
    kind: item.kind,
    organization_id: result.organization_id ?? item.organization_id,
    status: result.status,
    reason: result.reason ?? item.reason,
    created_at: result.created_at ?? item.created_at,
    updated_at: result.updated_at,
    revision: result.revision ?? item.revision,
    capability_keys: result.capability_keys ?? item.capability_keys,
    project_id: result.project_id ?? item.project_id,
  }, { endpoint });
}

export function useAccountAccessOverview() {
  const principalId = useShellStore((state) => state.principal?.actorId ?? null);
  return useQuery({
    queryKey: accountAccessKeys.overview(principalId ?? "signed-out"),
    enabled: principalId !== null,
    staleTime: 30_000,
    retry: (failureCount, reason) =>
      !(isDomainError(reason) && reason.httpStatus === 401) && failureCount < 1,
    queryFn: ({ signal }) => getAccountAccessOverview(signal),
  });
}
