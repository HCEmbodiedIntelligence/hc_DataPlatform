import { useState } from "react";
import { useShellStore } from "../../shared/scope/shell-store";
import type { ManagedAccount, ManagedAccountCreate } from "./contracts";
import type { AccessSearch } from "./query-codec";
import {
  useCreateManagedAccount,
  useDeleteManagedAccount,
  useManagedAccounts,
  useResetManagedAccountPassword,
  useSetManagedAccountRole,
  useSetManagedAccountStatus,
  useUnlockManagedAccount,
} from "./user-api";
import { UserManagementPanel } from "./UserManagementPanel";

export interface UserManagementContainerProps {
  readonly search: AccessSearch;
  readonly enabled: boolean;
  readonly canManage: boolean;
  readonly canUnlock: boolean;
  readonly onSearchChange: (patch: Partial<AccessSearch>) => void;
}

export function UserManagementContainer({
  canManage,
  canUnlock,
  enabled,
  onSearchChange,
  search,
}: Readonly<UserManagementContainerProps>) {
  const currentPrincipalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const [successMessage, setSuccessMessage] = useState<string>();
  const list = useManagedAccounts(
    {
      page: search.page,
      pageSize: search.pageSize,
      ...(search.q ? { query: search.q } : {}),
      ...(search.accountState !== "ALL" ? { state: search.accountState } : {}),
      ...(search.accountRole !== "ALL" ? { role: search.accountRole } : {}),
    },
    enabled,
  );
  const create = useCreateManagedAccount();
  const status = useSetManagedAccountStatus();
  const role = useSetManagedAccountRole();
  const password = useResetManagedAccountPassword();
  const remove = useDeleteManagedAccount();
  const unlock = useUnlockManagedAccount();
  const mutations = [create, status, role, password, remove, unlock] as const;
  const mutationPending = mutations.some((mutation) => mutation.isPending);
  const mutationError =
    mutations.find((mutation) => mutation.error !== null)?.error ?? null;

  const beginMutation = () => {
    setSuccessMessage(undefined);
    for (const mutation of mutations) mutation.reset();
  };

  return (
    <UserManagementPanel
      search={search}
      page={list.data}
      loading={list.isPending}
      fetching={list.isFetching}
      error={list.error}
      mutationPending={mutationPending}
      mutationError={mutationError}
      successMessage={successMessage}
      canManage={canManage}
      canUnlock={canUnlock}
      currentPrincipalId={currentPrincipalId}
      onSearchChange={onSearchChange}
      onRefresh={() => void list.refetch()}
      onDismissStatus={() => {
        setSuccessMessage(undefined);
        for (const mutation of mutations) mutation.reset();
      }}
      onCreate={async (command: ManagedAccountCreate) => {
        beginMutation();
        await create.mutateAsync(command);
        setSuccessMessage(`用户 ${command.username} 已创建。`);
      }}
      onStatusChange={async (account: ManagedAccount, enabled: boolean) => {
        beginMutation();
        await status.mutateAsync({ account, enabled });
        setSuccessMessage(
          `${account.display_name} 已${enabled ? "恢复" : "禁用"}。`,
        );
      }}
      onRoleChange={async (account, platformRole) => {
        beginMutation();
        await role.mutateAsync({ account, role: platformRole });
        setSuccessMessage(`${account.display_name} 的平台角色已更新。`);
      }}
      onResetPassword={async (account, newPassword, confirmPassword) => {
        beginMutation();
        const result = await password.mutateAsync({
          account,
          new_password: newPassword,
          confirm_password: confirmPassword,
        });
        setSuccessMessage(
          `${account.display_name} 的密码已重置，已撤销 ${result.sessions_revoked} 个会话。`,
        );
      }}
      onDelete={async (account) => {
        beginMutation();
        await remove.mutateAsync({ account });
        setSuccessMessage(`${account.display_name} 已删除，历史审计仍保留。`);
      }}
      onUnlock={async (account) => {
        beginMutation();
        await unlock.mutateAsync({ account });
        setSuccessMessage(
          `${account.display_name} 的解锁请求已完成；若存在临时锁定，现已清除。`,
        );
      }}
    />
  );
}
