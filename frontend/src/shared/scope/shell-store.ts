import { create } from "zustand";
import type { ActorSummary } from "../../entities/actor";
import type { AuthorizationSnapshot } from "../../entities/capability";
import { makeScopeKey, type Scope, type ScopeKey } from "../../entities/scope";
import { readPersistedSession, writePersistedSession } from "./session-storage";

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
  sessionOrganizations: readonly SessionOrganizationGrant[];
  bootstrapLoaded: boolean;
  platformCapabilities: readonly string[];
  capabilityRevision: number | null;
  setSession: (principal: ActorSummary | null, token: string | null) => void;
  updatePrincipal: (principal: ActorSummary) => void;
  beginScopeChange: () => void;
  setScope: (scope: Scope) => void;
  setAuthorizationLoading: () => void;
  setAuthorization: (snapshot: AuthorizationSnapshot) => void;
  setAuthorizationFailed: () => void;
  setSessionScopes: (
    scopes: readonly SessionScopeGrant[],
    capabilityRevision: number,
    platformCapabilities?: readonly string[],
    organizations?: readonly SessionOrganizationGrant[],
  ) => void;
  finishScopeChange: () => void;
  clearSensitiveState: () => void;
}

export interface SessionScopeGrant {
  readonly organizationId: string;
  readonly organizationName?: string;
  readonly projectId: string;
  readonly projectName?: string;
  readonly regionCodes: readonly string[];
  readonly projectWide: boolean;
  readonly capabilities: readonly string[];
}

export interface SessionOrganizationGrant {
  readonly organizationId: string;
  readonly organizationName: string;
  readonly memberStatus: "ACTIVE";
}

const UNSCOPED_KEY = "unscoped/-/-" as ScopeKey;
const restoredSession = readPersistedSession();
const restoredScope = restoredSession?.scope ?? null;

export const useShellStore = create<ShellState>((set) => ({
  principal: restoredSession?.principal ?? null,
  sessionToken: restoredSession?.sessionToken ?? null,
  scope: restoredScope,
  scopeKey: restoredScope === null ? UNSCOPED_KEY : makeScopeKey(restoredScope),
  scopeChanging: false,
  authorization: null,
  authorizationLoading: restoredSession !== null,
  authorizationFailed: false,
  sessionScopes: [],
  sessionOrganizations: [],
  bootstrapLoaded: false,
  platformCapabilities: [],
  capabilityRevision: null,
  setSession: (principal, sessionToken) =>
    set((state) => {
      const keepScope =
        principal !== null &&
        sessionToken !== null &&
        state.principal?.actorId === principal.actorId;
      const scope = keepScope ? state.scope : null;
      writePersistedSession(principal, sessionToken, scope);
      return {
        principal,
        sessionToken,
        scope,
        scopeKey: scope === null ? UNSCOPED_KEY : makeScopeKey(scope),
        authorization: null,
        authorizationLoading: false,
        authorizationFailed: false,
        sessionScopes: [],
        sessionOrganizations: [],
        bootstrapLoaded: false,
        platformCapabilities: [],
        capabilityRevision: null,
      };
    }),
  updatePrincipal: (principal) =>
    set((state) => {
      if (
        state.sessionToken === null ||
        state.principal === null ||
        state.principal.actorId !== principal.actorId
      ) {
        return state;
      }
      writePersistedSession(principal, state.sessionToken, state.scope);
      return { principal };
    }),
  beginScopeChange: () => set({ scopeChanging: true }),
  setScope: (scope) =>
    set((state) => {
      writePersistedSession(state.principal, state.sessionToken, scope);
      return { scope, scopeKey: makeScopeKey(scope), authorization: null };
    }),
  setAuthorizationLoading: () =>
    set({
      authorization: null,
      authorizationLoading: true,
      authorizationFailed: false,
    }),
  setAuthorization: (authorization) =>
    set({
      authorization,
      authorizationLoading: false,
      authorizationFailed: false,
    }),
  setAuthorizationFailed: () =>
    set({
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: true,
    }),
  setSessionScopes: (
    sessionScopes,
    capabilityRevision,
    platformCapabilities = [],
    sessionOrganizations = [],
  ) =>
    set({
      sessionScopes,
      sessionOrganizations,
      capabilityRevision,
      platformCapabilities,
      bootstrapLoaded: true,
    }),
  finishScopeChange: () => set({ scopeChanging: false }),
  clearSensitiveState: () =>
    set((state) => {
      writePersistedSession(state.principal, state.sessionToken, null);
      return {
        scope: null,
        scopeKey: UNSCOPED_KEY,
        authorization: null,
        authorizationLoading: false,
        authorizationFailed: false,
        sessionScopes: [],
        sessionOrganizations: [],
        platformCapabilities: [],
        capabilityRevision: null,
      };
    }),
}));

export function getShellState(): ShellState {
  return useShellStore.getState();
}
