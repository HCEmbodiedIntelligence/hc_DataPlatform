import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { request } from "../../shared/api/http-client";
import { makeQueryKey } from "../../shared/api/query-keys";
import { useShellStore } from "../../shared/scope/shell-store";
import type {
  ManagedAccount,
  ManagedAccountCreate,
  ManagedAccountPage,
  ManagedAccountPasswordReset,
  ManagedAccountPasswordResetResult,
  ManagedAccountState,
  PlatformAccountRole,
} from "./contracts";

const accountQueryDomain = "p18-platform-accounts";

export interface ManagedAccountFilters {
  readonly query?: string;
  readonly state?: ManagedAccountState;
  readonly role?: PlatformAccountRole;
  readonly page: number;
  readonly pageSize: number;
}

export async function listManagedAccounts(
  filters: ManagedAccountFilters,
  signal?: AbortSignal,
): Promise<ManagedAccountPage> {
  return request<ManagedAccountPage>({
    method: "GET",
    path: "/platform/accounts",
    scopeMode: "session",
    query: {
      query: filters.query,
      state: filters.state,
      role: filters.role,
      page: filters.page,
      page_size: filters.pageSize,
    },
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
}

export async function createManagedAccount(
  command: ManagedAccountCreate,
): Promise<ManagedAccount> {
  return request<ManagedAccount>({
    method: "POST",
    path: "/platform/accounts",
    scopeMode: "session",
    body: command,
    cache: "no-store",
  });
}

type VersionedAccountAction = {
  readonly account: ManagedAccount;
};

export async function setManagedAccountStatus(
  input: VersionedAccountAction & { readonly enabled: boolean },
): Promise<ManagedAccount> {
  return request<ManagedAccount>({
    method: "POST",
    path: `/platform/accounts/${encodeURIComponent(input.account.principal_id)}:${input.enabled ? "enable" : "disable"}`,
    scopeMode: "session",
    ifMatch: input.account.etag,
    cache: "no-store",
  });
}

export async function setManagedAccountRole(
  input: VersionedAccountAction & { readonly role: PlatformAccountRole },
): Promise<ManagedAccount> {
  return request<ManagedAccount>({
    method: "POST",
    path: `/platform/accounts/${encodeURIComponent(input.account.principal_id)}:role`,
    scopeMode: "session",
    ifMatch: input.account.etag,
    body: { platform_role: input.role },
    cache: "no-store",
  });
}

export async function resetManagedAccountPassword(
  input: VersionedAccountAction & ManagedAccountPasswordReset,
): Promise<ManagedAccountPasswordResetResult> {
  return request<ManagedAccountPasswordResetResult>({
    method: "POST",
    path: `/platform/accounts/${encodeURIComponent(input.account.principal_id)}:reset-password`,
    scopeMode: "session",
    ifMatch: input.account.etag,
    body: {
      new_password: input.new_password,
      confirm_password: input.confirm_password,
    },
    cache: "no-store",
  });
}

export async function deleteManagedAccount(
  input: VersionedAccountAction,
): Promise<void> {
  await request<void>({
    method: "DELETE",
    path: `/platform/accounts/${encodeURIComponent(input.account.principal_id)}`,
    scopeMode: "session",
    ifMatch: input.account.etag,
    cache: "no-store",
  });
}

export async function unlockManagedAccount(
  input: Pick<VersionedAccountAction, "account">,
): Promise<void> {
  await request<void>({
    method: "POST",
    path: `/platform/accounts/${encodeURIComponent(input.account.principal_id)}:unlock`,
    scopeMode: "session",
    cache: "no-store",
  });
}

function useAccountQueryIdentity() {
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const capabilityRevision = useShellStore(
    (state) => state.capabilityRevision ?? "authorization-unavailable",
  );
  return { principalId, capabilityRevision } as const;
}

export function useManagedAccounts(
  filters: ManagedAccountFilters,
  enabled: boolean,
): UseQueryResult<ManagedAccountPage, Error> {
  const identity = useAccountQueryIdentity();
  return useQuery<ManagedAccountPage, Error>({
    queryKey: makeQueryKey(accountQueryDomain, "list", {
      ...identity,
      filters,
    }),
    enabled: enabled && identity.principalId !== null,
    staleTime: 15_000,
    placeholderData: (previous) => previous,
    queryFn: ({ signal }) => listManagedAccounts(filters, signal),
  });
}

function useAccountMutation<Input, Output>(
  mutationFn: (input: Input) => Promise<Output>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [accountQueryDomain] });
    },
  });
}

export function useCreateManagedAccount() {
  return useAccountMutation(createManagedAccount);
}

export function useSetManagedAccountStatus() {
  return useAccountMutation(setManagedAccountStatus);
}

export function useSetManagedAccountRole() {
  return useAccountMutation(setManagedAccountRole);
}

export function useResetManagedAccountPassword() {
  return useAccountMutation(resetManagedAccountPassword);
}

export function useDeleteManagedAccount() {
  return useAccountMutation(deleteManagedAccount);
}

export function useUnlockManagedAccount() {
  return useAccountMutation(unlockManagedAccount);
}
