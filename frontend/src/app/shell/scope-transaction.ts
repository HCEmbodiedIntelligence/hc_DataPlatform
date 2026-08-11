import type { QueryClient } from '@tanstack/react-query';
import type { AuthorizationSnapshot } from '../../entities/capability';
import { makeScopeKey, type Scope } from '../../entities/scope';
import { cancelActiveTransports, releaseScopedResources } from '../../shared/api/transport-lifecycle';
import { useJobCenterStore } from '../../shared/jobs/job-center-store';
import { getShellState, useShellStore } from '../../shared/scope/shell-store';

export interface ScopeSwitchHooks {
  clearPageState?: () => void | Promise<void>;
  reloadNavigation?: (scope: Scope, signal: AbortSignal) => void | Promise<void>;
  resolveLegalPath?: (snapshot: AuthorizationSnapshot) => string | null;
  navigate?: (path: string) => void;
}

export async function executeScopeSwitch(
  queryClient: QueryClient,
  nextScope: Scope,
  loadAuthorization: (scope: Scope, signal: AbortSignal) => Promise<AuthorizationSnapshot>,
  hooks: ScopeSwitchHooks = {},
): Promise<void> {
  const oldScopeKey = getShellState().scopeKey;
  const transaction = new AbortController();
  const shell = useShellStore.getState();

  // 1. Block new writes.
  shell.beginScopeChange();
  try {
    // 2. Cancel old-scope HTTP requests, Query work and SSE subscriptions.
    await queryClient.cancelQueries({
      predicate: (query) => query.queryKey[1] === oldScopeKey,
    });
    await cancelActiveTransports();

    // 3. Release signed URLs, media, workers, Object URLs and WebGL resources.
    await releaseScopedResources();

    // 4. Clear in-memory selection and sensitive temporary state.
    await hooks.clearPageState?.();
    useJobCenterStore.getState().clear();
    useShellStore.getState().clearSensitiveState();
    queryClient.removeQueries({ predicate: (query) => query.queryKey[1] === oldScopeKey });

    // 5. Install the new scope and refetch authorization and navigation facts.
    useShellStore.getState().setScope(nextScope);
    useShellStore.getState().setAuthorizationLoading();
    const [snapshot] = await Promise.all([
      loadAuthorization(nextScope, transaction.signal),
      hooks.reloadNavigation?.(nextScope, transaction.signal),
    ]);
    if (snapshot.scopeKey !== makeScopeKey(nextScope)) throw new Error('Authorization scope mismatch');
    useShellStore.getState().setAuthorization(snapshot);

    // 6. Load only a route that is legal under the new authorization snapshot.
    const legalPath = hooks.resolveLegalPath?.(snapshot) ?? null;
    if (legalPath !== null) hooks.navigate?.(legalPath);
  } catch (error) {
    useShellStore.getState().setAuthorizationFailed();
    throw error;
  } finally {
    transaction.abort();
    useShellStore.getState().finishScopeChange();
  }
}
