import { useShellStore } from "../scope/shell-store";

export function useManagementAccess() {
  const platform = useShellStore(s => s.platformCapabilities.includes("platform.admin"));
  const organization = useShellStore(s => s.sessionOrganizations.some(org => org.capabilities?.includes("organization.admin"))
    || s.sessionScopes.some(scope => scope.capabilities.includes("organization.admin")));
  const project = useShellStore(s => s.sessionScopes.some(scope => scope.capabilities.includes("access.manage")
    || scope.capabilities.includes("role.project_admin")));
  return { organizations: platform || organization, projects: platform || organization || project };
}
