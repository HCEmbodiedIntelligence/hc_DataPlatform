import { makeScopeKey, type Scope } from "../../entities/scope";
import {
  getShellState,
  type SessionScopeGrant,
} from "../../shared/scope/shell-store";
import type { SessionBootstrap } from "./api";
import { getSessionBootstrap } from "./api";

function grantsFromBootstrap(
  bootstrap: SessionBootstrap,
): readonly SessionScopeGrant[] {
  return bootstrap.available_scopes.map((scope) => ({
    organizationId: scope.organization_id,
    ...(scope.organization_name
      ? { organizationName: scope.organization_name }
      : {}),
    projectId: scope.project_id,
    ...(scope.project_name ? { projectName: scope.project_name } : {}),
    regionCodes: scope.region_codes,
    projectWide: scope.project_wide,
    capabilities: scope.capabilities,
  }));
}

function organizationsFromBootstrap(bootstrap: SessionBootstrap) {
  const organizations = new Map(
    (bootstrap.available_organizations ?? []).map((organization) => [
      organization.organization_id,
      {
        organizationId: organization.organization_id,
        organizationName: organization.organization_name,
        memberStatus: organization.member_status,
      } as const,
    ]),
  );
  for (const scope of bootstrap.available_scopes) {
    if (!organizations.has(scope.organization_id)) {
      organizations.set(scope.organization_id, {
        organizationId: scope.organization_id,
        organizationName: scope.organization_name ?? scope.organization_id,
        memberStatus: "ACTIVE",
      });
    }
  }
  return [...organizations.values()];
}

function scopeAllowed(scope: Scope, grant: SessionScopeGrant): boolean {
  if (scope.organizationId !== grant.organizationId) return false;
  if (scope.projectId !== grant.projectId) return false;
  if (!scope.regionCode) return true;
  return grant.projectWide || grant.regionCodes.includes(scope.regionCode);
}

function preferredScope(
  grants: readonly SessionScopeGrant[],
): { readonly grant: SessionScopeGrant; readonly regionCode: string } | null {
  const projectId = import.meta.env.VITE_DEFAULT_PROJECT_ID?.trim();
  const organizationId = import.meta.env.VITE_DEFAULT_ORGANIZATION_ID?.trim();
  const regionCode = import.meta.env.VITE_DEFAULT_REGION_CODE?.trim();
  if (!projectId || !regionCode) return null;
  const matchingGrants = grants.filter(
    (candidate) =>
      candidate.projectId === projectId &&
      (!organizationId || candidate.organizationId === organizationId),
  );
  if (matchingGrants.length !== 1) return null;
  const grant = matchingGrants[0];
  if (!grant) return null;
  if (!grant.projectWide && !grant.regionCodes.includes(regionCode))
    return null;
  return { grant, regionCode };
}

function snapshot(
  scope: Scope,
  grant: SessionScopeGrant,
  capabilityRevision: number,
  platformCapabilities: readonly string[],
) {
  return {
    scopeKey: makeScopeKey(scope),
    roleVersion: String(capabilityRevision),
    capabilities: [
      ...new Set([...grant.capabilities, ...platformCapabilities]),
    ],
    fetchedAt: new Date().toISOString(),
  } as const;
}

export function installSessionBootstrap(bootstrap: SessionBootstrap): void {
  const store = getShellState();
  const grants = grantsFromBootstrap(bootstrap);
  store.setSessionScopes(
    grants,
    bootstrap.capability_revision,
    bootstrap.platform_capabilities,
    organizationsFromBootstrap(bootstrap),
  );
  if (grants.length === 0) {
    store.clearSensitiveState();
    store.setSessionScopes(
      grants,
      bootstrap.capability_revision,
      bootstrap.platform_capabilities,
      organizationsFromBootstrap(bootstrap),
    );
    return;
  }
  const current = store.scope;
  const preferred = preferredScope(grants);
  const currentGrant = current
    ? grants.find((candidate) => scopeAllowed(current, candidate))
    : undefined;
  const grant = currentGrant ?? preferred?.grant ?? grants[0];
  if (!grant) return;
  const currentRegion =
    current?.organizationId === grant.organizationId &&
    current.projectId === grant.projectId &&
    current.regionCode &&
    (grant.projectWide || grant.regionCodes.includes(current.regionCode))
      ? current.regionCode
      : preferred?.grant === grant
        ? preferred.regionCode
        : (grant.regionCodes[0] ?? (grant.projectWide ? "global" : undefined));
  const nextScope: Scope = {
    organizationId: grant.organizationId,
    projectId: grant.projectId,
    ...(currentRegion ? { regionCode: currentRegion } : {}),
  };
  store.setScope(nextScope);
  store.setAuthorization(
    snapshot(
      nextScope,
      grant,
      bootstrap.capability_revision,
      bootstrap.platform_capabilities,
    ),
  );
}

export async function loadRuntimeAuthorization(
  scope: Scope,
  signal: AbortSignal,
) {
  const bootstrap = await getSessionBootstrap({ signal });
  const grants = grantsFromBootstrap(bootstrap);
  getShellState().setSessionScopes(
    grants,
    bootstrap.capability_revision,
    bootstrap.platform_capabilities,
    organizationsFromBootstrap(bootstrap),
  );
  const grant = grants.find((candidate) => scopeAllowed(scope, candidate));
  if (!grant) throw new Error("SESSION_SCOPE_NOT_GRANTED");
  return snapshot(
    scope,
    grant,
    bootstrap.capability_revision,
    bootstrap.platform_capabilities,
  );
}
