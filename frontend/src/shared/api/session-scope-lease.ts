import { getShellState, useShellStore } from "../scope/shell-store";
import { hasSessionScopeAccess } from "../scope/session-access";
import type { Scope } from "../scope/model";
import { createManagedAbortController } from "./transport-lifecycle";

/** Own cancellation across a multi-request operation, including external signed transfers. */
export function createSessionScopeLease(scope: Scope, signal?: AbortSignal) {
  const captured = getShellState();
  const snapshot = Object.freeze({ ...scope });
  const { controller, release } = createManagedAbortController(signal, {
    cancelOnScopeChange: false,
  });
  if (!hasSessionScopeAccess(captured, snapshot))
    controller.abort("scope-not-granted");
  const unsubscribe = useShellStore.subscribe((state) => {
    if (
      state.sessionRevision !== captured.sessionRevision ||
      state.sessionToken !== captured.sessionToken ||
      !hasSessionScopeAccess(state, snapshot)
    )
      controller.abort("session-or-grant-changed");
  });
  return {
    signal: controller.signal,
    scope: snapshot,
    sessionRevision: captured.sessionRevision,
    release: () => {
      unsubscribe();
      release();
    },
  };
}
