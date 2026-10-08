import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeScopeKey } from "../../shared/scope/model";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { request } from "../../shared/api/http-client";
import { makeScopedQueryKey } from "../../shared/api/query-keys";
import { explicitScopeQueryMeta } from "../../shared/api/use-scoped-query";
import { executeScopeSwitch } from "./scope-transaction";

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("scope selection transaction", () => {
  it("preserves session grants and explicitly owned work while cancelling active page work", async () => {
    const a = { organizationId: "org", projectId: "a", regionCode: "r" };
    const b = { organizationId: "org", projectId: "b", regionCode: "r" };
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1/events",
      buildVersion: "switch-test",
      releaseEnv: "test",
    });
    const shell = useShellStore.getState();
    shell.setSession(
      { actorId: "switch", displayName: "switch", roleIds: [] },
      "switch-token",
    );
    shell.setScope(a);
    const grants = [a, b].map((scope) => ({
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCodes: [scope.regionCode],
      projectWide: false,
      capabilities: ["dataset.read"],
    }));
    shell.setSessionScopes(grants, 7, ["platform.account.read"]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const signals: AbortSignal[] = [];
    const completes: ((response: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_input, init: RequestInit) => {
        signals.push(init.signal!);
        return new Promise<Response>((resolve) => completes.push(resolve));
      }),
    );
    const activeKey = makeScopedQueryKey(a, "active-page", "capacity", "id");
    const independentKey = makeScopedQueryKey(a, "workspace", "capacity", "id");
    const active = client
      .fetchQuery({
        queryKey: activeKey,
        queryFn: ({ signal }) =>
          request({
            method: "GET",
            path: "/projects/a/storage/capacity",
            scope: a,
            signal,
          }),
      })
      .catch(() => "cancelled");
    const independent = client.fetchQuery({
      queryKey: independentKey,
      meta: explicitScopeQueryMeta,
      queryFn: ({ signal }) =>
        request({
          method: "GET",
          path: "/projects/a/storage/capacity",
          scopeMode: "explicit",
          scope: a,
          signal,
        }),
    });
    await executeScopeSwitch(client, b, async (scope) => ({
      scopeKey: makeScopeKey(scope),
      roleVersion: "7",
      capabilities: ["dataset.read"],
      fetchedAt: "2026-09-30T00:00:00Z",
    }));
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
    expect(useShellStore.getState().sessionScopes).toEqual(grants);
    expect(useShellStore.getState().platformCapabilities).toEqual([
      "platform.account.read",
    ]);
    expect(useShellStore.getState().scope).toEqual(b);
    expect(
      client.getQueryCache().find({ queryKey: activeKey }),
    ).toBeUndefined();
    expect(
      client.getQueryCache().find({ queryKey: independentKey }),
    ).toBeDefined();
    completes[0]!(new Response(JSON.stringify({ page: "stale" })));
    completes[1]!(new Response(JSON.stringify({ workspace: "a" })));
    expect(await active).toBe("cancelled");
    await expect(independent).resolves.toEqual({ workspace: "a" });
    expect(client.getQueryData(independentKey)).toEqual({ workspace: "a" });
    client.clear();
  });
});
