import type { Scope } from "./model";
import type { getShellState } from "./shell-store";

/** Session grants authorize a workspace independently of the current UI selection. */
export function hasSessionScopeAccess(
  session: ReturnType<typeof getShellState>,
  scope: Scope | null,
): boolean {
  if (!session.sessionToken || !scope?.organizationId) return false;
  if (!scope.projectId) {
    if (scope.regionCode !== undefined) return false;
    return (
      session.sessionOrganizations.some(
        (grant) => grant.organizationId === scope.organizationId,
      ) ||
      session.sessionScopes.some(
        (grant) => grant.organizationId === scope.organizationId,
      )
    );
  }
  return session.sessionScopes.some(
    (grant) =>
      grant.organizationId === scope.organizationId &&
      grant.projectId === scope.projectId &&
      (!scope.regionCode ||
        grant.projectWide ||
        grant.regionCodes.includes(scope.regionCode)),
  );
}
