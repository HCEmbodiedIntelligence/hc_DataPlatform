import { getShellState, useShellStore } from "./shell-store";

function currentOrganization(
  state: ReturnType<typeof getShellState>,
): string | null {
  return (
    state.scope?.organizationId ??
    state.sessionOrganizations[0]?.organizationId ??
    state.sessionScopes[0]?.organizationId ??
    null
  );
}

/** Selection adapters belong to session UI; pure feature clients require explicit scope. */
export function useCurrentOrganizationId(): string | null {
  return useShellStore(currentOrganization);
}

export function getCurrentOrganizationId(): string {
  const organizationId = currentOrganization(getShellState());
  if (!organizationId) throw new Error("当前会话没有可用的组织范围。");
  return organizationId;
}
