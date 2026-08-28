import { useCallback, useMemo } from "react";
import { CANONICAL_CAPABILITIES } from "../../entities/capability";
import type { ScopeKey } from "../../entities/scope";
import { useShellStore } from "../scope/shell-store";

const PLATFORM_ADMIN_CAPABILITY = "platform.admin";

const runtimeCapabilityImplications: Readonly<
  Record<string, readonly string[]>
> = {
  "collection.upload": ["upload.read", "upload.manage"],
  "ingest.upload": ["upload.read", "upload.manage"],
  "annotation.write": [
    "annotation_task.read",
    "annotation_task.claim",
    "episode.read",
    "annotation.edit",
    "annotation.save",
    "annotation.submit",
    "annotation_draft.edit",
  ],
  "annotation.review": ["annotation_task.read", "episode.read"],
  "project.access.manage": ["access.read", "access.manage"],
  "datasets.read": ["dataset.read", "dataset_version.read", "episode.read"],
  "datasets.write": ["dataset.create"],
  "datasets.publish": ["dataset_version.publish"],
  "tag_schema.write": [
    "data_schema.read",
    "data_schema.create",
    "data_schema.publish",
  ],
};

export function expandGrantedCapabilities(
  capabilities: readonly string[],
): ReadonlySet<string> {
  const expanded = new Set(capabilities);
  for (const capability of capabilities) {
    for (const implied of runtimeCapabilityImplications[capability] ?? []) {
      expanded.add(implied);
    }
  }
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
      scopeMatches &&
      (granted.has(PLATFORM_ADMIN_CAPABILITY) || granted.has(capability)),
    [failed, granted, loading, scopeMatches],
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
