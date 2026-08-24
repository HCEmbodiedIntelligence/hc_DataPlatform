// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "./shell-store";

afterEach(() => {
  useShellStore.getState().setSession(null, null);
  window.sessionStorage.clear();
});

describe("shell principal updates", () => {
  it("updates the same principal without clearing scope or authorization", () => {
    const store = useShellStore.getState();
    const scope = {
      organizationId: "org-profile",
      projectId: "project-profile",
      regionCode: "region-profile",
    };
    store.setSession(
      { actorId: "principal-profile", displayName: "旧名称", roleIds: [] },
      "profile-token",
    );
    store.setScope(scope);
    store.setSessionScopes(
    [
      {
        organizationId: scope.organizationId,
        projectId: scope.projectId,
          regionCodes: [scope.regionCode],
          projectWide: false,
          capabilities: ["dataset.read"],
        },
      ],
      7,
    );
    store.setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "7",
      capabilities: ["dataset.read"],
      fetchedAt: "2026-08-20T00:00:00Z",
    });

    store.updatePrincipal({
      actorId: "principal-profile",
      displayName: "新名称",
      roleIds: [],
    });

    const next = useShellStore.getState();
    expect(next.principal?.displayName).toBe("新名称");
    expect(next.sessionToken).toBe("profile-token");
    expect(next.scope).toEqual(scope);
    expect(next.authorization?.capabilities).toEqual(["dataset.read"]);
    expect(next.sessionScopes).toHaveLength(1);
    expect(window.sessionStorage.getItem("hc-platform-current-session")).toContain(
      "新名称",
    );
  });

  it("ignores an update for a different principal", () => {
    const store = useShellStore.getState();
    store.setSession(
      { actorId: "principal-current", displayName: "当前账户", roleIds: [] },
      "profile-token",
    );

    store.updatePrincipal({
      actorId: "principal-other",
      displayName: "其他账户",
      roleIds: [],
    });

    expect(useShellStore.getState().principal).toMatchObject({
      actorId: "principal-current",
      displayName: "当前账户",
    });
  });
});
