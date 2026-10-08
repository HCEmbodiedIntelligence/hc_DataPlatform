import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  explicitScopeQueryMeta,
  useScopeQueryRevision,
  useScopedQuery,
} from "../../../shared/api/use-scoped-query";
import { makeScopedQueryKey } from "../../../shared/api/scoped-query-keys";
import { useShellStore } from "../../../shared/scope/shell-store";
import { hasSessionScopeAccess } from "../../../shared/scope/session-access";
import {
  invalidateScopedDomain,
  useScopedMutation,
} from "../../../shared/api/use-scoped-mutation";
import * as api from "./client";

function organizationScope(organizationId: string | null) {
  return organizationId ? { organizationId } : null;
}

export function useScopedRobots(
  organizationId: string | null,
  filters: Readonly<Record<string, string>> = {},
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robots",
    "organization-list",
    { organizationId, filters },
    {
      enabled: Boolean(organizationId),
      staleTime: 30_000,
      queryFn: ({ signal }) => api.listRobots(organizationId!, filters, signal),
    },
  );
}

export function useScopedRobotBootstrap(
  organizationId: string | null,
  robotId: string | null,
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robots",
    "organization-bootstrap",
    { organizationId, robotId },
    {
      enabled: Boolean(organizationId && robotId),
      staleTime: 15_000,
      queryFn: ({ signal }) =>
        api.getRobotBootstrap(organizationId!, robotId!, signal),
    },
  );
}

export function useScopedRobotBootstraps(
  organizationId: string | null,
  robotIds: readonly string[],
) {
  const scope = organizationScope(organizationId);
  const authorized = useShellStore((state) =>
    hasSessionScopeAccess(state, scope),
  );
  const revision = useScopeQueryRevision();
  return useQueries({
    queries: robotIds.map((robotId) => ({
      queryKey: makeScopedQueryKey(
        authorized ? scope! : { organizationId: "" },
        "robots",
        "organization-bootstrap",
        { organizationId, robotId },
        authorized ? revision : `${revision}:ungranted`,
      ),
      meta: explicitScopeQueryMeta,
      enabled: authorized,
      staleTime: 15_000,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.getRobotBootstrap(organizationId!, robotId, signal),
    })),
  });
}

function useRobotMutation<T, V>(
  organizationId: string | null,
  operation: (organizationId: string, intent: V) => Promise<T>,
) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: V) => operation(scope.organizationId, intent),
    {
      onSuccess: (_data, _intent, binding) => {
        void invalidateScopedDomain(client, binding, "robots");
      },
    },
  );
}

export function useScopedCreateRobot(organizationId: string | null) {
  return useRobotMutation(organizationId, api.createRobot);
}
export function useScopedUpdateRobot(organizationId: string | null) {
  return useRobotMutation(organizationId, api.updateRobot);
}
export function useScopedTransitionRobotLifecycle(
  organizationId: string | null,
) {
  return useRobotMutation(organizationId, api.transitionRobotLifecycle);
}
export function useScopedDeleteRobot(organizationId: string | null) {
  return useRobotMutation(organizationId, api.deleteRobot);
}
