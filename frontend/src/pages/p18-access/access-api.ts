import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Scope } from "../../entities/scope";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { makeQueryKey } from "../../shared/api/query-keys";
import { useShellStore } from "../../shared/scope/shell-store";
import type {
  AccessDecision,
  AccessDecisionCommand,
  AccessRequestKind,
  CapabilityRequest,
  CapabilityRequestList,
  MembershipRequest,
  MembershipRequestList,
} from "./contracts";

const accessQueryDomain = "p18-access";

export type AccessScope = Scope & {
  readonly projectId: string;
};

function contractMismatch(message: string) {
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

function projectRoot(scope: AccessScope): string {
  return `/organizations/${encodeURIComponent(scope.organizationId)}/projects/${encodeURIComponent(scope.projectId)}`;
}

function assertProjectListScope(
  scope: AccessScope,
  items: readonly {
    readonly organization_id: string;
    readonly project_id: string;
  }[],
): void {
  if (
    items.some(
      (item) =>
        item.organization_id !== scope.organizationId ||
        item.project_id !== scope.projectId,
    )
  ) {
    throw contractMismatch(
      "申请列表响应包含当前项目之外的数据，页面已安全关闭。",
    );
  }
}

export async function listMembershipRequests(
  scope: AccessScope,
  signal?: AbortSignal,
): Promise<MembershipRequestList> {
  const result = await request<MembershipRequestList>({
    method: "GET",
    path: `${projectRoot(scope)}/membership-requests`,
    scope,
    ...(signal ? { signal } : {}),
    cache: "no-store",
  });
  if (!Array.isArray(result.items))
    throw contractMismatch("项目加入申请列表不符合正式合同。");
  assertProjectListScope(scope, result.items);
  return result;
}

export async function listCapabilityRequests(
  scope: AccessScope,
  signal?: AbortSignal,
): Promise<CapabilityRequestList> {
  const result = await request<CapabilityRequestList>({
    method: "GET",
    path: `${projectRoot(scope)}/capability-requests`,
    scope,
    ...(signal ? { signal } : {}),
    cache: "no-store",
  });
  if (!Array.isArray(result.items))
    throw contractMismatch("权限申请列表不符合正式合同。");
  assertProjectListScope(scope, result.items);
  return result;
}

export interface AccessDecisionInput {
  readonly kind: AccessRequestKind;
  readonly action: AccessDecision;
  readonly projectId: string;
  readonly requestId: string;
  readonly reason: string | null;
}

export async function decideAccessRequest(
  input: AccessDecisionInput,
  scope: AccessScope,
): Promise<MembershipRequest | CapabilityRequest> {
  if (scope.projectId !== input.projectId) {
    throw contractMismatch("当前项目已变化，请重新打开申请后再操作。");
  }
  const resource =
    input.kind === "membership" ? "membership-requests" : "capability-requests";
  const body = { reason: input.reason } satisfies AccessDecisionCommand;
  const result = await request<MembershipRequest | CapabilityRequest>({
    method: "POST",
    path: `${projectRoot(scope)}/${resource}/${encodeURIComponent(input.requestId)}:${input.action}`,
    body,
    scope,
    idempotencyKey: globalThis.crypto.randomUUID(),
    cache: "no-store",
  });
  if (
    result.organization_id !== scope.organizationId ||
    result.project_id !== input.projectId ||
    result.request_id !== input.requestId
  ) {
    throw contractMismatch(
      "审批响应的申请或项目作用域不匹配，页面未采纳该结果。",
    );
  }
  return result;
}

function useAccessRequestIdentity() {
  const scope = useShellStore((state) => state.scope);
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const authorizationRevision = useShellStore(
    (state) => state.authorization?.roleVersion ?? "authorization-unavailable",
  );
  return {
    scope:
      scope?.projectId && scope.organizationId ? (scope as AccessScope) : null,
    projectId: scope?.projectId ?? null,
    principalId,
    authorizationRevision,
  } as const;
}

export function useMembershipRequests(enabled = true) {
  const identity = useAccessRequestIdentity();
  return useQuery({
    queryKey: makeQueryKey(accessQueryDomain, "membership-requests", identity),
    enabled: enabled && identity.scope !== null,
    staleTime: 15_000,
    queryFn: ({ signal }) => listMembershipRequests(identity.scope!, signal),
  });
}

export function useCapabilityRequests(enabled = true) {
  const identity = useAccessRequestIdentity();
  return useQuery({
    queryKey: makeQueryKey(accessQueryDomain, "capability-requests", identity),
    enabled: enabled && identity.scope !== null,
    staleTime: 15_000,
    queryFn: ({ signal }) => listCapabilityRequests(identity.scope!, signal),
  });
}

export function useAccessDecision() {
  const queryClient = useQueryClient();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const activeScope = useShellStore((state) =>
    state.scope?.projectId && state.scope.organizationId
      ? (state.scope as AccessScope)
      : null,
  );
  return useMutation({
    mutationFn: async (input: AccessDecisionInput) => {
      if (!activeScope || input.projectId !== activeScope.projectId) {
        throw contractMismatch("当前项目已变化，请重新打开申请后再操作。");
      }
      return decideAccessRequest(input, activeScope);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: [accessQueryDomain, scopeKey],
      });
    },
  });
}
