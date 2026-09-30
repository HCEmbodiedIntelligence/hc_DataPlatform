// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useShellStore } from "../scope/shell-store";
import { useScopedQuery } from "./use-scoped-query";
import {
  invalidateScopedDomain,
  scopedMutationKey,
  useScopedMutation,
} from "./use-scoped-mutation";

const a = { organizationId: "org-a" };
const b = { organizationId: "org-b" };
let client: QueryClient;
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "scope-test", displayName: "test", roleIds: [] },
    "scope-token",
  );
  store.setSessionScopes(
    [],
    1,
    [],
    [a, b].map((scope) => ({
      ...scope,
      organizationName: scope.organizationId,
      memberStatus: "ACTIVE" as const,
    })),
  );
  store.setScope(b);
});
afterEach(() => {
  cleanup();
  client.clear();
  useShellStore.getState().setSession(null, null);
});

describe("workspace query and mutation ownership", () => {
  it("supports two equal identifiers in independent scopes and masks revoked cached data", async () => {
    const loadA = vi.fn(async () => "model-a");
    const loadB = vi.fn(async () => "model-b");
    const { result } = renderHook(
      () => ({
        a: useScopedQuery(a, "model", "version", "same-id", {
          queryFn: loadA,
          staleTime: Infinity,
        }),
        b: useScopedQuery(b, "model", "version", "same-id", {
          queryFn: loadB,
          staleTime: Infinity,
        }),
      }),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.a.data).toBe("model-a");
      expect(result.current.b.data).toBe("model-b");
    });
    act(() =>
      useShellStore.getState().setScope({ organizationId: "other-selection" }),
    );
    expect(result.current.a.data).toBe("model-a");
    expect(result.current.b.data).toBe("model-b");
    act(() =>
      useShellStore
        .getState()
        .setSessionScopes(
          [],
          1,
          [],
          [{ ...b, organizationName: "B", memberStatus: "ACTIVE" }],
        ),
    );
    expect(result.current.a.data).toBeUndefined();
    expect(result.current.b.data).toBe("model-b");
    expect(loadA).toHaveBeenCalledTimes(1);
    expect(loadB).toHaveBeenCalledTimes(1);
  });

  it("does not expose another session's cached response for the same scope", async () => {
    const load = vi.fn().mockResolvedValueOnce("old-session-data");
    const { result } = renderHook(
      () =>
        useScopedQuery(a, "model", "version", "id", {
          queryFn: load,
          staleTime: Infinity,
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toBe("old-session-data"));
    act(() =>
      useShellStore
        .getState()
        .setSession(
          { actorId: "new", displayName: "new", roleIds: [] },
          "new-token",
        ),
    );
    expect(result.current.data).toBeUndefined();
  });

  it("freezes mutation scope and cache identity before the component switches scope", async () => {
    let complete!: (value: string) => void;
    const operation = vi.fn(
      (_scope, _intent: string) =>
        new Promise<string>((resolve) => {
          complete = resolve;
        }),
    );
    const observed = vi.fn();
    const { result, rerender } = renderHook(
      ({ scope }) =>
        useScopedMutation(scope, operation, {
          onSuccess: (data, _intent, binding) => {
            observed(binding);
            client.setQueryData(
              scopedMutationKey(binding, "model", "version", "id"),
              data,
            );
          },
        }),
      { wrapper, initialProps: { scope: a } },
    );
    let pending!: Promise<string>;
    act(() => {
      pending = result.current.mutateAsync("change");
    });
    await waitFor(() => expect(operation).toHaveBeenCalledTimes(1));
    rerender({ scope: b });
    await act(async () => {
      complete("saved-in-a");
      await pending;
    });
    expect(operation.mock.calls[0]?.[0]).toEqual(a);
    expect(observed.mock.calls[0]?.[0].scope).toEqual(a);
    const caches = client.getQueryCache().getAll();
    expect(caches).toHaveLength(1);
    expect(caches[0]?.queryKey[1]).toBe("org-a/-/-");
    expect(caches[0]?.state.data).toBe("saved-in-a");
  });

  it("suppresses mutation cache updates after logout or revocation", async () => {
    let complete!: (value: string) => void;
    const saved = vi.fn();
    const operation = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          complete = resolve;
        }),
    );
    const { result } = renderHook(
      () => useScopedMutation(a, operation, { onSuccess: saved }),
      { wrapper },
    );
    let pending!: Promise<string>;
    act(() => {
      pending = result.current.mutateAsync("change");
    });
    await waitFor(() => expect(operation).toHaveBeenCalledTimes(1));
    act(() => useShellStore.getState().setSession(null, null));
    await act(async () => {
      complete("stale");
      await pending;
    });
    expect(saved).not.toHaveBeenCalled();
  });

  it("refreshes the same workspace after authorization revision changes in flight", async () => {
    let value = "before";
    let complete!: () => void;
    const operation = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          complete = () => {
            value = "after";
            resolve(value);
          };
        }),
    );
    const { result } = renderHook(
      () => ({
        query: useScopedQuery(a, "model", "version", "id", {
          queryFn: async () => value,
          staleTime: Infinity,
        }),
        mutation: useScopedMutation(a, operation, {
          onSuccess: (_data, _intent, binding) => {
            void invalidateScopedDomain(client, binding, "model");
          },
        }),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.query.data).toBe("before"));
    let pending!: Promise<string>;
    act(() => {
      pending = result.current.mutation.mutateAsync("change");
    });
    await waitFor(() => expect(operation).toHaveBeenCalledTimes(1));
    act(() =>
      useShellStore.getState().setSessionScopes(
        [],
        2,
        [],
        [a, b].map((scope) => ({
          ...scope,
          organizationName: scope.organizationId,
          memberStatus: "ACTIVE" as const,
        })),
      ),
    );
    await waitFor(() => expect(result.current.query.data).toBe("before"));
    await act(async () => {
      complete();
      await pending;
    });
    await waitFor(() => expect(result.current.query.data).toBe("after"));
  });

  it("rejects an old rendered callback after another session is installed", async () => {
    const operation = vi.fn(async () => "should-not-run");
    const { result } = renderHook(() => useScopedMutation(a, operation), {
      wrapper,
    });
    const oldCallback = result.current.mutateAsync;
    act(() => {
      useShellStore
        .getState()
        .setSession(
          { actorId: "new", displayName: "new", roleIds: [] },
          "new-token",
        );
      useShellStore
        .getState()
        .setSessionScopes(
          [],
          1,
          [],
          [{ ...a, organizationName: "A", memberStatus: "ACTIVE" }],
        );
    });
    await act(async () => {
      await expect(oldCallback("old-intent")).rejects.toMatchObject({
        blockedReasons: [{ code: "SESSION_CHANGED" }],
      });
    });
    expect(operation).not.toHaveBeenCalled();
  });

  it("rejects a queued mutation if the session changes before its function starts", async () => {
    const operation = vi.fn(async () => "should-not-run");
    const { result } = renderHook(() => useScopedMutation(a, operation), {
      wrapper,
    });
    let pending!: Promise<string>;
    act(() => {
      pending = result.current.mutateAsync("old-intent");
      useShellStore.getState().setSession(null, null);
    });
    await act(async () => {
      await expect(pending).rejects.toMatchObject({
        blockedReasons: [{ code: "SESSION_CHANGED" }],
      });
    });
    expect(operation).not.toHaveBeenCalled();
  });

  it.each(["scope", "session", "revocation"] as const)(
    "does not expose a previous mutation result after ownership changes: %s",
    async (change) => {
      const { result, rerender } = renderHook(
        ({ scope }) => useScopedMutation(scope, async () => "private-result"),
        { wrapper, initialProps: { scope: a } },
      );
      await act(async () => {
        await result.current.mutateAsync("intent");
      });
      await waitFor(() => expect(result.current.data).toBe("private-result"));
      if (change === "scope") rerender({ scope: b });
      else
        act(() => {
          if (change === "session")
            useShellStore.getState().setSession(null, null);
          else useShellStore.getState().setSessionScopes([], 1);
        });
      expect(result.current.data).toBeUndefined();
      expect(result.current.variables).toBeUndefined();
      expect(result.current.isIdle).toBe(true);
    },
  );
});
