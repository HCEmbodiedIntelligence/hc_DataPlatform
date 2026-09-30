import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { useShellStore } from "../scope/shell-store";
import { hasSessionScopeAccess } from "../scope/session-access";
import type { Scope } from "../scope/model";
import { makeScopedQueryKey, type SharedQueryKey } from "./scoped-query-keys";

export const explicitScopeQueryMeta = Object.freeze({
  scopeBinding: "explicit",
});

export function useScopeQueryRevision(): string {
  const sessionRevision = useShellStore((state) => state.sessionRevision);
  const capabilityRevision = useShellStore((state) => state.capabilityRevision);
  return `session:${sessionRevision}:capabilities:${capabilityRevision ?? "pending"}`;
}

/** No shell selection is consulted: null disables this workspace rather than selecting another. */
export function useScopedQuery<T>(
  scope: Scope | null,
  domain: string,
  resource: string,
  identity: unknown,
  options: Omit<
    UseQueryOptions<T, Error, T, SharedQueryKey>,
    "queryKey" | "meta" | "enabled"
  > & { enabled?: boolean },
) {
  const revision = useScopeQueryRevision();
  const authorized = useShellStore((state) =>
    hasSessionScopeAccess(state, scope),
  );
  const enabled = authorized && options.enabled !== false;
  return useQuery({
    ...options,
    // Revocation may precede the capability revision. Quarantine old data immediately.
    queryKey: makeScopedQueryKey(
      authorized ? scope! : { organizationId: "" },
      domain,
      resource,
      identity,
      authorized ? revision : `${revision}:ungranted`,
    ),
    meta: explicitScopeQueryMeta,
    enabled,
  });
}
