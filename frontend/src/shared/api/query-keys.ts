/** Compatibility selection adapter; pure construction lives in scoped-query-keys.ts. */
import { getShellState } from "../scope/shell-store";
import { makeScopedQueryKey, type SharedQueryKey } from "./scoped-query-keys";
export { makeScopedQueryKey, normalizeFilters } from "./scoped-query-keys";
export type { SharedQueryKey } from "./scoped-query-keys";

export function makeQueryKey(
  domain: string,
  resource: string,
  identityOrFilters: unknown,
  snapshotOrRevision?: string,
): SharedQueryKey {
  return makeScopedQueryKey(
    getShellState().scopeKey,
    domain,
    resource,
    identityOrFilters,
    snapshotOrRevision,
  );
}
