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
    projectId: scope.project_id,
    regionCodes: scope.region_codes,
    projectWide: scope.project_wide,
    capabilities: scope.capabilities,
  }));
}

function scopeAllowed(scope: Scope, grant: SessionScopeGrant): boolean {
  if (scope.projectId !== grant.projectId) return false;
  if (!scope.regionCode) return true;
  return grant.projectWide || grant.regionCodes.includes(scope.regionCode);
}

function snapshot(
  scope: Scope,
  grant: SessionScopeGrant,
  capabilityRevision: number,
) {
  return {
    scopeKey: makeScopeKey(scope),
    roleVersion: String(capabilityRevision),
    capabilities: grant.capabilities,
    fetchedAt: new Date().toISOString(),
  } as const;
}

export function installSessionBootstrap(bootstrap: SessionBootstrap): void {
  const store = getShellState();
  const grants = grantsFromBootstrap(bootstrap);
  store.setSessionScopes(grants, bootstrap.capability_revision);
  if (grants.length === 0) {
    store.clearSensitiveState();
    return;
  }
  const current = store.scope;
  const grant =
    grants.find((candidate) => current && scopeAllowed(current, candidate)) ??
    grants[0];
  if (!grant) return;
  const currentRegion =
    current?.projectId === grant.projectId &&
    current.regionCode &&
    (grant.projectWide || grant.regionCodes.includes(current.regionCode))
      ? current.regionCode
      : grant.regionCodes[0];
  const nextScope: Scope = {
    organizationId: "",
    projectId: grant.projectId,
    ...(currentRegion ? { regionCode: currentRegion } : {}),
  };
  store.setScope(nextScope);
  store.setAuthorization(
    snapshot(nextScope, grant, bootstrap.capability_revision),
  );
}

export async function loadRuntimeAuthorization(
  scope: Scope,
  signal: AbortSignal,
) {
  const bootstrap = await getSessionBootstrap(signal);
  const grants = grantsFromBootstrap(bootstrap);
  getShellState().setSessionScopes(grants, bootstrap.capability_revision);
  const grant = grants.find((candidate) => scopeAllowed(scope, candidate));
  if (!grant) throw new Error("SESSION_SCOPE_NOT_GRANTED");
  return snapshot(scope, grant, bootstrap.capability_revision);
}
