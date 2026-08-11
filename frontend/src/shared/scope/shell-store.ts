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
  setSession: (principal: ActorSummary | null, token: string | null) => void;
  beginScopeChange: () => void;
  setScope: (scope: Scope) => void;
  setAuthorizationLoading: () => void;
  setAuthorization: (snapshot: AuthorizationSnapshot) => void;
  setAuthorizationFailed: () => void;
  finishScopeChange: () => void;
  clearSensitiveState: () => void;
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
  setSession: (principal, sessionToken) => set({ principal, sessionToken }),
  beginScopeChange: () => set({ scopeChanging: true }),
  setScope: (scope) => set({ scope, scopeKey: makeScopeKey(scope), authorization: null }),
  setAuthorizationLoading: () =>
    set({ authorization: null, authorizationLoading: true, authorizationFailed: false }),
  setAuthorization: (authorization) =>
    set({ authorization, authorizationLoading: false, authorizationFailed: false }),
  setAuthorizationFailed: () =>
    set({ authorization: null, authorizationLoading: false, authorizationFailed: true }),
  finishScopeChange: () => set({ scopeChanging: false }),
  clearSensitiveState: () => set({ authorization: null }),
}));

export function getShellState(): ShellState {
  return useShellStore.getState();
}
