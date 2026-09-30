import {
  useMutation,
  type MutateOptions,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";
import { makeScopedQueryKey } from "./scoped-query-keys";
import { getShellState, useShellStore } from "../scope/shell-store";
import { createDomainError } from "./domain-error";
import { hasSessionScopeAccess } from "../scope/session-access";
import { makeScopeKey, type Scope } from "../scope/model";
import { useScopeQueryRevision } from "./use-scoped-query";

export interface ScopeMutationBinding {
  readonly scope: Scope | null;
  readonly revision: string;
  readonly sessionRevision: number;
}

export function scopedMutationKey(
  binding: ScopeMutationBinding,
  domain: string,
  resource: string,
  identity: unknown,
) {
  if (!binding.scope) throw new Error("Mutation scope is missing");
  return makeScopedQueryKey(
    binding.scope,
    domain,
    resource,
    identity,
    binding.revision,
  );
}

export function invalidateScopedDomain(
  client: QueryClient,
  binding: ScopeMutationBinding,
  domain: string,
) {
  if (!binding.scope) return;
  const key = scopedMutationKey(binding, domain, "", null);
  return client.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === domain &&
      query.queryKey[1] === key[1] &&
      typeof query.queryKey[4] === "string" &&
      query.queryKey[4].startsWith(`session:${binding.sessionRevision}:`),
  });
}

export function invalidateScopedResource(
  client: QueryClient,
  binding: ScopeMutationBinding,
  domain: string,
  resource: string,
  identity: unknown,
) {
  return client.invalidateQueries({
    queryKey: scopedMutationKey(binding, domain, resource, identity).slice(
      0,
      4,
    ),
    predicate: (query) =>
      typeof query.queryKey[4] === "string" &&
      query.queryKey[4].startsWith(`session:${binding.sessionRevision}:`),
  });
}

/** Capture scope in mutation variables before asynchronous work or a UI rerender. */
export function useScopedMutation<T, V>(
  scope: Scope | null,
  operation: (scope: Scope, intent: V) => Promise<T>,
  options: {
    onSuccess?: (data: T, intent: V, binding: ScopeMutationBinding) => void;
    gcTime?: number;
  } = {},
) {
  const revision = useScopeQueryRevision();
  const sessionRevision = useShellStore((state) => state.sessionRevision);
  const authorized = useShellStore((state) =>
    hasSessionScopeAccess(state, scope),
  );
  const scopeKey = makeScopeKey(scope ?? { organizationId: "" });
  type BoundIntent = {
    readonly intent: V;
    readonly binding: ScopeMutationBinding;
  };
  const mutation = useMutation<T, Error, BoundIntent, ScopeMutationBinding>({
    ...(options.gcTime === undefined ? {} : { gcTime: options.gcTime }),
    onMutate: ({ binding }) => binding,
    mutationFn: ({ intent, binding }) => {
      if (getShellState().sessionRevision !== binding.sessionRevision) {
        return Promise.reject(
          createDomainError({
            code: "PRECONDITION_FAILED",
            message: "请求所属登录会话已失效",
            blockedReasons: [
              { code: "SESSION_CHANGED", message: "登录会话已变化" },
            ],
            fieldErrors: [],
            operationErrors: [],
            requestId: null,
            retryable: false,
            httpStatus: null,
          }),
        );
      }
      if (!binding.scope)
        return Promise.reject(new Error("当前会话没有可用的组织范围。"));
      return operation(binding.scope, intent);
    },
    onSuccess: (data, { intent, binding }) => {
      const session = getShellState();
      if (
        session.sessionRevision === binding.sessionRevision &&
        hasSessionScopeAccess(session, binding.scope)
      ) {
        options.onSuccess?.(data, intent, binding);
      }
    },
  });
  const binding = mutation.variables?.binding;
  const ownsResult =
    !binding ||
    (binding.sessionRevision === sessionRevision &&
      authorized &&
      binding.scope !== null &&
      makeScopeKey(binding.scope) === scopeKey);
  useEffect(() => {
    if (!ownsResult) mutation.reset();
  }, [ownsResult, mutation.reset]);
  // Ownership changes must not expose another workspace/session's mutation result or error.
  const visible = ownsResult
    ? mutation
    : {
        ...mutation,
        status: "idle" as const,
        data: undefined,
        error: null,
        variables: undefined,
        isIdle: true as const,
        isPending: false as const,
        isSuccess: false as const,
        isError: false as const,
      };
  function bind(intent: V): BoundIntent {
    return {
      intent,
      binding: Object.freeze({
        scope: scope ? Object.freeze({ ...scope }) : null,
        revision,
        sessionRevision,
      }),
    };
  }
  function adaptOptions(
    callbacks?: MutateOptions<T, Error, V, ScopeMutationBinding>,
  ): MutateOptions<T, Error, BoundIntent, ScopeMutationBinding> | undefined {
    if (!callbacks) return undefined;
    return {
      ...(callbacks.onSuccess
        ? {
            onSuccess: (data, vars, context) =>
              callbacks.onSuccess!(data, vars.intent, context),
          }
        : {}),
      ...(callbacks.onError
        ? {
            onError: (error, vars, context) =>
              callbacks.onError!(error, vars.intent, context),
          }
        : {}),
      ...(callbacks.onSettled
        ? {
            onSettled: (data, error, vars, context) =>
              callbacks.onSettled!(data, error, vars.intent, context),
          }
        : {}),
    };
  }
  return {
    ...visible,
    variables: visible.variables?.intent,
    mutate: (
      intent: V,
      callbacks?: MutateOptions<T, Error, V, ScopeMutationBinding>,
    ) => mutation.mutate(bind(intent), adaptOptions(callbacks)),
    mutateAsync: (
      intent: V,
      callbacks?: MutateOptions<T, Error, V, ScopeMutationBinding>,
    ) => mutation.mutateAsync(bind(intent), adaptOptions(callbacks)),
  };
}
