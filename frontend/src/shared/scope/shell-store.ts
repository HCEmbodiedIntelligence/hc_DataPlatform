import { create } from 'zustand';
import type { ActorSummary } from '../../entities/actor';
import type { AuthorizationSnapshot } from '../../entities/capability';
import { makeScopeKey, type Scope, type ScopeKey } from '../../entities/scope';

interface ShellState {
  principal: ActorSummary | null;
  sessionToken: string | null;
  scope: Scope | null;
  scopeKey: ScopeKey;
  scopeChanging: boolean;
  authorization: AuthorizationSnapshot | null;
  authorizationLoading: boolean;
  authorizationFailed: boolean;
  sessionScopes: readonly SessionScopeGrant[];
  capabilityRevision: number | null;
  setSession: (principal: ActorSummary | null, token: string | null) => void;
  beginScopeChange: () => void;
  setScope: (scope: Scope) => void;
  setAuthorizationLoading: () => void;
  setAuthorization: (snapshot: AuthorizationSnapshot) => void;
  setAuthorizationFailed: () => void;
  setSessionScopes: (
    scopes: readonly SessionScopeGrant[],
    capabilityRevision: number,
  ) => void;
  finishScopeChange: () => void;
  clearSensitiveState: () => void;
}

export interface SessionScopeGrant {
  readonly projectId: string;
  readonly regionCodes: readonly string[];
  readonly projectWide: boolean;
  readonly capabilities: readonly string[];
}

const UNSCOPED_KEY = 'unscoped/-/-' as ScopeKey;

export const useShellStore = create<ShellState>((set) => ({
  principal: null,
  sessionToken: null,
  scope: null,
  scopeKey: UNSCOPED_KEY,
  scopeChanging: false,
  authorization: null,
  authorizationLoading: false,
  authorizationFailed: false,
  sessionScopes: [],
  capabilityRevision: null,
  setSession: (principal, sessionToken) =>
    set({
      principal,
      sessionToken,
      ...(sessionToken === null
        ? {
            scope: null,
            scopeKey: UNSCOPED_KEY,
            authorization: null,
            authorizationLoading: false,
            authorizationFailed: false,
            sessionScopes: [],
            capabilityRevision: null,
          }
        : {}),
    }),
  beginScopeChange: () => set({ scopeChanging: true }),
  setScope: (scope) => set({ scope, scopeKey: makeScopeKey(scope), authorization: null }),
  setAuthorizationLoading: () =>
    set({ authorization: null, authorizationLoading: true, authorizationFailed: false }),
  setAuthorization: (authorization) =>
    set({ authorization, authorizationLoading: false, authorizationFailed: false }),
  setAuthorizationFailed: () =>
    set({ authorization: null, authorizationLoading: false, authorizationFailed: true }),
  setSessionScopes: (sessionScopes, capabilityRevision) =>
    set({ sessionScopes, capabilityRevision }),
  finishScopeChange: () => set({ scopeChanging: false }),
  clearSensitiveState: () =>
    set({
      scope: null,
      scopeKey: UNSCOPED_KEY,
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      sessionScopes: [],
      capabilityRevision: null,
    }),
}));

export function getShellState(): ShellState {
  return useShellStore.getState();
}
