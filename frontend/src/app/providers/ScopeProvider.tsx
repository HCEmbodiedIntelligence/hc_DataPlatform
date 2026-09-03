import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { AuthorizationSnapshot } from '../../entities/capability';
import type { Scope } from '../../entities/scope';
import { executeScopeSwitch, type ScopeSwitchHooks } from '../shell/scope-transaction';

interface ScopeContextValue {
  switchScope: (
    nextScope: Scope,
    loadAuthorization: (scope: Scope, signal: AbortSignal) => Promise<AuthorizationSnapshot>,
    hooks?: ScopeSwitchHooks,
  ) => Promise<void>;
}

const ScopeContext = createContext<ScopeContextValue | null>(null);

export function ScopeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const value = useMemo<ScopeContextValue>(
    () => ({
      switchScope: (nextScope, loadAuthorization, hooks = {}) =>
        executeScopeSwitch(queryClient, nextScope, loadAuthorization, hooks),
    }),
    [queryClient],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

// Provider and hook are intentionally colocated to keep the context private.
// eslint-disable-next-line react-refresh/only-export-components
export function useScope(): ScopeContextValue {
  const context = useContext(ScopeContext);
  if (context === null) throw new Error('useScope must be used inside ScopeProvider');
  return context;
}
