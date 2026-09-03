import { afterEach, describe, expect, it, vi } from "vitest";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import type { SessionBootstrap } from "./api";
import { installSessionBootstrap } from "./runtime-scope";

const bootstrap: SessionBootstrap = {
  principal: {
    principal_id: "principal-admin",
    username: "hc-admin",
    display_name: "HC Admin",
    status: "ACTIVE",
    created_at: "2026-08-20T00:00:00Z",
  },
  available_scopes: [
    {
      organization_id: "organization-first",
      project_id: "project-first",
      region_codes: [],
      project_wide: true,
      capabilities: ["dashboard.read"],
    },
    {
      organization_id: "organization-preferred",
      project_id: "project-preferred",
      region_codes: [],
      project_wide: true,
      capabilities: ["dashboard.read", "upload.read"],
    },
  ],
  platform_capabilities: [],
  capability_revision: 10,
};

afterEach(() => {
  vi.unstubAllEnvs();
  useShellStore.getState().setSession(null, null);
});

describe("installSessionBootstrap", () => {
  it("installs a configured project-wide default scope without manual region input", () => {
    vi.stubEnv("VITE_DEFAULT_PROJECT_ID", "project-preferred");
    vi.stubEnv("VITE_DEFAULT_REGION_CODE", "cn-default");

    installSessionBootstrap(bootstrap);

    expect(useShellStore.getState().scope).toEqual({
      organizationId: "organization-preferred",
      projectId: "project-preferred",
      regionCode: "cn-default",
    });
    expect(useShellStore.getState().authorization).toMatchObject({
      scopeKey: makeScopeKey({
        organizationId: "organization-preferred",
        projectId: "project-preferred",
        regionCode: "cn-default",
      }),
      roleVersion: "10",
      capabilities: ["dashboard.read", "upload.read"],
    });
  });

  it("ignores a configured scope that is not granted", () => {
    vi.stubEnv("VITE_DEFAULT_PROJECT_ID", "project-not-granted");
    vi.stubEnv("VITE_DEFAULT_REGION_CODE", "cn-default");

    installSessionBootstrap(bootstrap);

    expect(useShellStore.getState().scope).toEqual({
      organizationId: "organization-first",
      projectId: "project-first",
    });
  });

  it("keeps an authorized project-wide persisted scope without requiring a region", () => {
    useShellStore.getState().setScope({
      organizationId: "organization-preferred",
      projectId: "project-preferred",
    });

    installSessionBootstrap(bootstrap);

    expect(useShellStore.getState().scope).toEqual({
      organizationId: "organization-preferred",
      projectId: "project-preferred",
    });
  });

  it("merges the global platform admin marker into every real project snapshot", () => {
    installSessionBootstrap({
      ...bootstrap,
      platform_capabilities: ["platform.admin"],
    });

    expect(useShellStore.getState().sessionScopes).toHaveLength(2);
    expect(useShellStore.getState().authorization?.capabilities).toContain(
      "platform.admin",
    );
  });
});
