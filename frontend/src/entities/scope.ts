export interface Scope {
  organizationId: string;
  projectId?: string;
  regionCode?: string;
}

export type ScopeKey = string & { readonly __scopeKey: unique symbol };

function assertScopePart(value: string, name: string): void {
  if (!value.trim() || value.includes('/')) {
    throw new Error(`${name} must be a non-empty stable ID without '/'`);
  }
}

export function makeScopeKey(scope: Scope): ScopeKey {
  // Runtime SessionBootstrap currently has no organization field. An empty
  // organization is therefore an absent dimension, not a fabricated identity.
  if (scope.organizationId.trim()) assertScopePart(scope.organizationId, 'organizationId');
  if (scope.projectId !== undefined) assertScopePart(scope.projectId, 'projectId');
  if (scope.regionCode !== undefined) assertScopePart(scope.regionCode, 'regionCode');
  return [scope.organizationId.trim() || '-', scope.projectId ?? '-', scope.regionCode ?? '-'].join('/') as ScopeKey;
}

export function sameScope(left: Scope | null, right: Scope | null): boolean {
  if (left === null || right === null) return left === right;
  return makeScopeKey(left) === makeScopeKey(right);
}
