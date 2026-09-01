import { useCallback, useMemo } from "react";
import { CANONICAL_CAPABILITIES } from "../../entities/capability";
import type { ScopeKey } from "../../entities/scope";
import { useShellStore } from "../scope/shell-store";

const PLATFORM_ADMIN_CAPABILITY = "platform.admin";

export function expandGrantedCapabilities(
  capabilities: readonly string[],
): ReadonlySet<string> {
  const expanded = new Set(capabilities);
  if (expanded.has(PLATFORM_ADMIN_CAPABILITY)) {
    for (const capability of CANONICAL_CAPABILITIES) expanded.add(capability);
  }
  return expanded;
}

export interface CapabilitiesResult {
  has: (capability: string) => boolean;
  loading: boolean;
  failed: boolean;
}

export function useCapabilities(): CapabilitiesResult {
  const snapshot = useShellStore((state) => state.authorization);
  const platformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  const hasScope = useShellStore((state) => state.scope !== null);
  const currentScopeKey = useShellStore((state) => state.scopeKey);
  const loading = useShellStore((state) => state.authorizationLoading);
  const authorizationFailed = useShellStore(
    (state) => state.authorizationFailed,
  );
  const scopeMatches =
    hasScope && snapshot !== null && snapshot.scopeKey === currentScopeKey;
  const expiresAt =
    snapshot?.expiresAt === undefined ? null : Date.parse(snapshot.expiresAt);
  const expired =
    expiresAt !== null &&
    (!Number.isFinite(expiresAt) || expiresAt <= Date.now());
  const granted = useMemo(
    () =>
      expandGrantedCapabilities(
        scopeMatches && snapshot ? snapshot.capabilities : [],
      ),
    [scopeMatches, snapshot],
  );
  const globalGranted = useMemo(
    () => expandGrantedCapabilities(platformCapabilities),
    [platformCapabilities],
  );
  const failed =
    authorizationFailed ||
    (hasScope &&
      ((!loading && snapshot === null) ||
        (snapshot !== null && !scopeMatches) ||
        expired));
  const has = useCallback(
    (capability: string) =>
      !loading &&
      !failed &&
      (globalGranted.has(PLATFORM_ADMIN_CAPABILITY) ||
        globalGranted.has(capability) ||
        (scopeMatches &&
          (granted.has(PLATFORM_ADMIN_CAPABILITY) || granted.has(capability)))),
    [failed, globalGranted, granted, loading, scopeMatches],
  );
  return { has, loading, failed };
}

export function useOrganizationCapabilities(
  organizationId: string | null,
): CapabilitiesResult {
  const sessionScopes = useShellStore((state) => state.sessionScopes);
  const platformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  const loading = useShellStore(
    (state) => !state.bootstrapLoaded || state.authorizationLoading,
  );
  const failed = useShellStore((state) => state.authorizationFailed);
  const granted = useMemo(
    () =>
      expandGrantedCapabilities([
        ...platformCapabilities,
        ...sessionScopes
          .filter((scope) => scope.organizationId === organizationId)
          .flatMap((scope) => scope.capabilities),
      ]),
    [organizationId, platformCapabilities, sessionScopes],
  );
  const has = useCallback(
    (capability: string) =>
      Boolean(organizationId) &&
      !loading &&
      !failed &&
      (granted.has(PLATFORM_ADMIN_CAPABILITY) || granted.has(capability)),
    [failed, granted, loading, organizationId],
  );
  return { has, loading, failed };
}

export interface BlockedActionReason {
  code: string;
  message: string;
}

export type AllowedAction =
  | string
  | {
      action: string;
      allowed: boolean;
      blockedReasons?: readonly BlockedActionReason[];
    };

export interface AllowedActionsSnapshot {
  scopeKey: ScopeKey;
  actions: readonly AllowedAction[];
}

function isAllowedActionsSnapshot(
  actions: readonly AllowedAction[] | AllowedActionsSnapshot,
): actions is AllowedActionsSnapshot {
  return (
    !Array.isArray(actions) && "scopeKey" in actions && "actions" in actions
  );
}

export function useAllowedActions(
  actions: readonly AllowedAction[] | AllowedActionsSnapshot | null | undefined,
): {
  can: (action: string) => boolean;
  blockedReason: (action: string) => string | null;
} {
  const scopeKey = useShellStore((state) => state.scopeKey);
  return useMemo(() => {
    const snapshot =
      actions && isAllowedActionsSnapshot(actions) ? actions : null;
    const scopeMatches = snapshot === null || snapshot.scopeKey === scopeKey;
    const list: readonly AllowedAction[] =
      actions && !isAllowedActionsSnapshot(actions)
        ? actions
        : (snapshot?.actions ?? []);
    const byAction = new Map<
      string,
      { allowed: boolean; reason: string | null }
    >();
    if (scopeMatches) {
      for (const item of list) {
        if (typeof item === "string")
          byAction.set(item, { allowed: true, reason: null });
        else
          byAction.set(item.action, {
            allowed: item.allowed,
            reason:
              item.blockedReasons?.map((reason) => reason.message).join("；") ||
              null,
          });
      }
    }
    return {
      can: (action: string) => byAction.get(action)?.allowed === true,
      blockedReason: (action: string) => {
        if (!scopeMatches) return "资源作用域与当前作用域不匹配";
        const decision = byAction.get(action);
        if (!decision) return "当前资源未允许此操作";
        return decision.allowed
          ? null
          : (decision.reason ?? "当前资源阻止此操作");
      },
    };
  }, [actions, scopeKey]);
}
