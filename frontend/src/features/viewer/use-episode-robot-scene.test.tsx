// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { getRobotBootstrap } from "../robots/api/client";
import {
  getRobotModelVersion,
  listRobotModelAssets,
} from "../robot-models/api/client";
import { useEpisodeRobotScene } from "./use-episode-robot-scene";
import type { StreamDescriptor } from "./types";

const scopes = [{ organizationId: "org-a" }, { organizationId: "org-b" }];
const stream: StreamDescriptor = {
  id: "joints",
  canonicalPath: "/joint_state",
  displayName: "joints",
  modality: "joint_state",
  schema: { id: "joint-vector", version: "1" },
  startNs: "0",
  endNs: "1000000000",
  availability: "ready",
  windowSource: {
    loadWindow: async () => ({
      generation: 0,
      timestampsNs: ["0"],
      values: [[0]],
      series: [{ id: "source_joint", displayName: "source_joint" }],
    }),
  },
};
let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function envelope(organizationId: string, path: string) {
  const scope = { organization_id: organizationId };
  const registryScope = { ...scope, project_id: `project-${organizationId}` };
  const common = {
    scope: registryScope,
    request_id: "workspace-test",
    contract_version: "1",
  };
  if (path.endsWith("/bootstrap"))
    return {
      ...common,
      scope,
      data: {
        robot: {
          id: "same-robot",
          display_name: organizationId,
          serial_no: organizationId,
          lifecycle_status: "ACTIVE",
          connectivity: {
            state: "ONLINE",
            observed_at: null,
            source: null,
            reason_code: null,
          },
        },
        etag: '"robot:1"',
        topology_revision: "1",
        allowed_actions: ["VIEW"],
        effective_model_binding: {
          id: "binding",
          scope_type: "ROBOT_INSTANCE",
          scope_id: "same-robot",
          robot_model_version_id: "same-version",
          valid_from: "2026-09-01T00:00:00Z",
          valid_to: null,
          etag: '"binding:1"',
        },
      },
    };
  if (path.endsWith("/assets"))
    return {
      scope: registryScope,
      request_id: common.request_id,
      items: [
        {
          asset_id: "urdf",
          relative_path: "robot.urdf",
          role: "URDF",
          media_type: "application/xml",
          size_bytes: 10,
          sha256: "a".repeat(64),
          created_at: "2026-09-01T00:00:00Z",
        },
      ],
    };
  if (path.endsWith("/joint-mappings"))
    return {
      scope: registryScope,
      request_id: common.request_id,
      items: [
        {
          source_joint_name: "source_joint",
          target_joint_name: "urdf_joint",
          direction: "SAME",
        },
      ],
      mapping_hash: "b".repeat(64),
    };
  return {
    ...common,
    data: {
      id: "same-version",
      robot_model_id: `model-${organizationId}`,
      version_label: "1",
      lifecycle: "PUBLISHED",
      asset_availability: "AVAILABLE",
      publish_readiness: "READY",
      asset_manifest_hash: "a".repeat(64),
      validation_input_hash: "b".repeat(64),
      etag: '"version:1"',
      allowed_actions: ["VIEW"],
      blocked_reasons: [],
    },
  };
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1/events",
    buildVersion: "workspace-test",
    releaseEnv: "test",
  });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const shell = useShellStore.getState();
  shell.setSession(
    { actorId: "viewer", displayName: "viewer", roleIds: [] },
    "viewer-token",
  );
  shell.setSessionScopes(
    scopes.map((scope) => ({
      organizationId: scope.organizationId,
      projectId: `project-${scope.organizationId}`,
      regionCodes: [],
      projectWide: true,
      capabilities: ["robot_model.read"],
    })),
    1,
    [],
    scopes.map((scope) => ({
      ...scope,
      organizationName: scope.organizationId,
      memberStatus: "ACTIVE" as const,
    })),
  );
  shell.setScope({
    organizationId: "unrelated-active",
    projectId: "project",
    regionCode: "region",
  });
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const organizationId = path.match(/\/organizations\/([^/]+)\//u)?.[1];
    if (!organizationId) throw new Error(`unexpected path: ${path}`);
    return new Response(JSON.stringify(envelope(organizationId, path)));
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("explicit organization replay", () => {
  it("uses adapter provenance and never loads an unavailable model reference", () => {
    const { result } = renderHook(
      () =>
        useEpisodeRobotScene("legacy-placeholder", stream, scopes[0]!, {
          kind: "current-binding-compatibility",
          title: "机器人模型预览",
          availability: "unavailable",
          reason: "没有冻结或兼容模型引用",
        }),
      { wrapper },
    );
    expect(result.current.robotScene).toBeUndefined();
    expect(result.current.robotSceneUnavailableReason).toBe(
      "没有冻结或兼容模型引用",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads equal robot/version IDs in two workspaces without touching the active scope", async () => {
    const { result } = renderHook(
      () => ({
        a: useEpisodeRobotScene("same-robot", stream, scopes[0]!),
        b: useEpisodeRobotScene("same-robot", stream, scopes[1]!),
      }),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.a.robotScene?.modelRef.modelId).toBe("model-org-a");
      expect(result.current.b.robotScene?.modelRef.modelId).toBe("model-org-b");
    });
    expect(fetchMock).toHaveBeenCalledTimes(8);
    for (const [input, init] of fetchMock.mock.calls) {
      const organizationId = String(input).match(
        /\/organizations\/([^/]+)\//u,
      )?.[1];
      const headers = new Headers((init as RequestInit).headers);
      expect(headers.get("X-Organization-Id")).toBe(organizationId);
      expect(headers.get("X-Project-Id")).toBeNull();
      expect(headers.get("X-Region-Code")).toBeNull();
    }
    act(() =>
      useShellStore
        .getState()
        .setScope({ organizationId: "another-selection" }),
    );
    expect(result.current.a.robotScene?.modelRef.modelId).toBe("model-org-a");
    expect(result.current.b.robotScene?.modelRef.modelId).toBe("model-org-b");
    expect(fetchMock).toHaveBeenCalledTimes(8);
    expect(useShellStore.getState().scope).toEqual({
      organizationId: "another-selection",
    });
  });

  it("disables null-scoped replay instead of selecting the shell organization", async () => {
    const { result } = renderHook(
      () => useEpisodeRobotScene("same-robot", stream, null),
      { wrapper },
    );
    expect(result.current.robotScene).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("removes scene data immediately when an organization grant is revoked", async () => {
    const { result } = renderHook(
      () => useEpisodeRobotScene("same-robot", stream, scopes[0]!),
      { wrapper },
    );
    await waitFor(() => expect(result.current.robotScene).toBeDefined());
    act(() =>
      useShellStore
        .getState()
        .setSessionScopes(
          [],
          1,
          [],
          [{ ...scopes[1]!, organizationName: "B", memberStatus: "ACTIVE" }],
        ),
    );
    expect(result.current.robotScene).toBeUndefined();
  });

  it.each([getRobotBootstrap, getRobotModelVersion, listRobotModelAssets])(
    "rejects a well-shaped response from another organization",
    async (load) => {
      fetchMock.mockImplementation(
        async () =>
          new Response(
            JSON.stringify(
              envelope(
                "org-b",
                load === getRobotBootstrap
                  ? "/bootstrap"
                  : load === listRobotModelAssets
                    ? "/assets"
                    : "/version",
              ),
            ),
          ),
      );
      await expect(
        load(
          "org-a",
          load === getRobotBootstrap ? "same-robot" : "same-version",
        ),
      ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
    },
  );
});
