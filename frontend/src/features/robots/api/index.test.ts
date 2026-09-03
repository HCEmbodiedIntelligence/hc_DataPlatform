import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  createRobot,
  getRobotBootstrap,
  robotBootstrapWireSchema,
  robotsPageWireSchema,
} from ".";

const organizationId = "org-robotics";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function bootstrapEnvelope() {
  return {
    data: {
      robot: {
        id: "robot-1",
        display_name: "XR-01",
        serial_no: "XR-01-SN",
        lifecycle_status: "ACTIVE",
        connectivity: {
          state: "ONLINE",
          observed_at: "2026-08-31T08:00:00Z",
          source: "edge-agent",
          reason_code: null,
        },
      },
      etag: '"robot-1:2"',
      topology_revision: "topology-1",
      effective_model_binding: null,
      allowed_actions: ["VIEW", "EDIT", "BIND_MODEL"],
    },
    scope: { organization_id: organizationId },
    request_id: "p15-client-test",
    contract_version: "2026-08-31",
  };
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p15-robot-assets-api-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "robot-manager", displayName: "机器人管理员", roleIds: [] },
      "p15-robotics-session",
    );
  useShellStore.getState().setSessionScopes(
    [],
    1,
    [],
    [
      {
        organizationId,
        organizationName: "机器人组织",
        memberStatus: "ACTIVE",
      },
    ],
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("organization robot asset client", () => {
  it("creates and loads a robot without a selected project", async () => {
    const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
      Promise.resolve(jsonResponse(bootstrapEnvelope())),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createRobot(organizationId, {
        displayName: "XR-01",
        serialNo: "XR-01-SN",
        lifecycleStatus: "ACTIVE",
        connectivityState: "ONLINE",
        connectivitySource: "edge-agent",
        idempotencyKey: "create-robot-key",
      }),
    ).resolves.toMatchObject({ id: "robot-1", etag: '"robot-1:2"' });
    await expect(getRobotBootstrap("robot-1")).resolves.toMatchObject({
      id: "robot-1",
      displayName: "XR-01",
    });

    const [createUrl, createInit] = fetchMock.mock.calls[0] ?? [];
    expect(createUrl).toBe("/api/v1/organizations/org-robotics/robots");
    expect(new Headers(createInit?.headers).get("X-Project-Id")).toBeNull();
    expect(new Headers(createInit?.headers).get("Idempotency-Key")).toBe(
      "create-robot-key",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/organizations/org-robotics/robots/robot-1/bootstrap",
    );
  });

  it("rejects project-shaped robot envelopes", () => {
    const valid = bootstrapEnvelope();
    expect(robotBootstrapWireSchema.safeParse(valid).success).toBe(true);
    expect(
      robotBootstrapWireSchema.safeParse({
        ...valid,
        scope: { project_id: "project-a", region_code: "region-a" },
      }).success,
    ).toBe(false);

    expect(
      robotsPageWireSchema.safeParse({
        items: [valid.data.robot],
        page_info: {
          has_next_page: false,
          has_previous_page: false,
          start_cursor: null,
          end_cursor: null,
        },
        snapshot_at: "2026-08-31T08:00:00Z",
        scope: valid.scope,
        request_id: "list",
        contract_version: "2026-08-31",
      }).success,
    ).toBe(true);
  });
});
