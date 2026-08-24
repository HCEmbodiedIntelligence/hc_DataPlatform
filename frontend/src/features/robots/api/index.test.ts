import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  createRobot,
  createRobotComponent,
  createRobotMaintenanceRecord,
  listRobotMaintenanceRecords,
  transitionRobotLifecycle,
  transitionRobotComponentLifecycle,
  updateRobotComponent,
  updateRobot,
} from ".";

const scope = {
  projectId: "project-robotics",
  regionCode: "cn-shanghai-1",
} as const;

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
          observed_at: "2026-08-21T08:00:00Z",
          source: "edge-agent",
          reason_code: null,
        },
      },
      etag: '"robot-1:2"',
      topology_revision: "topology-1",
      effective_model_binding: null,
      allowed_actions: ["VIEW", "EDIT", "TRANSITION"],
    },
    scope: { project_id: scope.projectId, region_code: scope.regionCode },
    request_id: "p15-client-test",
    contract_version: "2026-08-21",
  };
}

function componentMutationEnvelope() {
  return {
    data: {
      component: {
        id: "component-1",
        robot_id: "robot-1",
        parent_component_id: null,
        component_model_id: "arm-model-1",
        component_type: "ARM",
        display_name: "主机械臂",
        serial_no: "ARM-01",
        lifecycle_status: "ACTIVE",
        sort_order: "1",
      },
      robot_etag: '"robot-1:3"',
      topology_revision: "topology-2",
    },
    scope: { project_id: scope.projectId, region_code: scope.regionCode },
    request_id: "p15-component-client-test",
    contract_version: "2026-08-21",
  };
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p15-robotics-api-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "robot-manager", displayName: "机器人管理员", roleIds: [] },
      "p15-robotics-session",
    );
  useShellStore.getState().setScope({
    organizationId: "org-robotics",
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("P15 robot management client", () => {
  it("sends create, edit and lifecycle commands with exact scope, CAS and idempotency metadata", async () => {
    const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
      jsonResponse(bootstrapEnvelope()),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createRobot(scope, {
        displayName: "XR-01",
        serialNo: "XR-01-SN",
        lifecycleStatus: "DRAFT",
        connectivityState: "OFFLINE",
        connectivitySource: "factory-import",
        idempotencyKey: "create-robot-key",
      }),
    ).resolves.toMatchObject({ id: "robot-1", etag: '"robot-1:2"' });
    await expect(
      updateRobot(scope, {
        robotId: "robot-1",
        etag: '"robot-1:2"',
        displayName: "XR-01A",
        connectivityState: "ONLINE",
        idempotencyKey: "update-robot-key",
      }),
    ).resolves.toMatchObject({ displayName: "XR-01" });
    await expect(
      transitionRobotLifecycle(scope, {
        robotId: "robot-1",
        etag: '"robot-1:2"',
        lifecycleStatus: "MAINTENANCE",
        reason: "例行维护",
        idempotencyKey: "transition-robot-key",
      }),
    ).resolves.toMatchObject({ lifecycle: "ACTIVE" });

    const createCall = fetchMock.mock.calls[0];
    if (!createCall) throw new Error("missing create request");
    const [createUrl, createInit] = createCall;
    if (!createInit) throw new Error("missing create request init");
    expect(createUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/robots",
    );
    expect(JSON.parse(String(createInit.body))).toEqual({
      display_name: "XR-01",
      serial_no: "XR-01-SN",
      lifecycle_status: "DRAFT",
      connectivity_state: "OFFLINE",
      connectivity_source: "factory-import",
    });
    expect(new Headers(createInit.headers).get("Idempotency-Key")).toBe(
      "create-robot-key",
    );

    const updateCall = fetchMock.mock.calls[1];
    if (!updateCall) throw new Error("missing update request");
    const [updateUrl, updateInit] = updateCall;
    if (!updateInit) throw new Error("missing update request init");
    expect(updateUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/robots/robot-1",
    );
    expect(updateInit.method).toBe("PATCH");
    expect(new Headers(updateInit.headers).get("If-Match")).toBe('"robot-1:2"');
    expect(new Headers(updateInit.headers).get("Idempotency-Key")).toBe(
      "update-robot-key",
    );

    const transitionCall = fetchMock.mock.calls[2];
    if (!transitionCall) throw new Error("missing transition request");
    const [transitionUrl, transitionInit] = transitionCall;
    if (!transitionInit) throw new Error("missing transition request init");
    expect(transitionUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/robots/robot-1:transition",
    );
    expect(transitionInit.method).toBe("POST");
    expect(JSON.parse(String(transitionInit.body))).toEqual({
      lifecycle_status: "MAINTENANCE",
      reason: "例行维护",
    });
    expect(new Headers(transitionInit.headers).get("If-Match")).toBe(
      '"robot-1:2"',
    );
    expect(new Headers(transitionInit.headers).get("X-Project-Id")).toBe(
      scope.projectId,
    );
  });

  it("loads and writes persisted maintenance records without accepting malformed wire data", async () => {
    const page = {
      items: [
        {
          id: "maintenance-1",
          robot_id: "robot-1",
          component_id: null,
          event_type: "MAINTENANCE",
          previous_lifecycle_status: null,
          lifecycle_status: null,
          summary: "更换末端夹具润滑脂",
          details: "使用指定型号润滑脂。",
          actor_id: "robot-manager",
          occurred_at: "2026-08-21T08:10:00Z",
        },
      ],
      page_info: {
        has_next_page: false,
        has_previous_page: false,
        start_cursor: null,
        end_cursor: null,
      },
      snapshot_at: "2026-08-21T08:10:00Z",
      scope: { project_id: scope.projectId, region_code: scope.regionCode },
      request_id: "maintenance-list",
      contract_version: "2026-08-21",
    };
    const fetchMock = vi
      .fn(
        (_input: string, _init?: RequestInit): Promise<Response> =>
          Promise.resolve(jsonResponse({})),
      )
      .mockResolvedValueOnce(jsonResponse(page))
      .mockResolvedValueOnce(
        jsonResponse({
          data: page.items[0],
          scope: page.scope,
          request_id: "maintenance-write",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ ...page, unexpected: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listRobotMaintenanceRecords(scope, "robot-1"),
    ).resolves.toMatchObject({
      items: [expect.objectContaining({ id: "maintenance-1" })],
    });
    await expect(
      createRobotMaintenanceRecord(scope, {
        robotId: "robot-1",
        summary: "更换末端夹具润滑脂",
        details: "使用指定型号润滑脂。",
        idempotencyKey: "maintenance-write-key",
      }),
    ).resolves.toMatchObject({ id: "maintenance-1" });
    const writeCall = fetchMock.mock.calls[1];
    if (!writeCall) throw new Error("missing maintenance write request");
    const [writeUrl, writeInit] = writeCall;
    if (!writeInit) throw new Error("missing maintenance write request init");
    expect(writeUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/robots/robot-1/maintenance-records",
    );
    expect(JSON.parse(String(writeInit.body))).toEqual({
      summary: "更换末端夹具润滑脂",
      details: "使用指定型号润滑脂。",
    });
    expect(new Headers(writeInit.headers).get("Idempotency-Key")).toBe(
      "maintenance-write-key",
    );

    await expect(
      listRobotMaintenanceRecords(scope, "robot-1"),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });

  it("creates, edits and retires components only with the parent robot CAS boundary", async () => {
    const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
      jsonResponse(componentMutationEnvelope()),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createRobotComponent(scope, {
        robotId: "robot-1",
        etag: '"robot-1:2"',
        componentModelId: "arm-model-1",
        componentType: "ARM",
        displayName: "主机械臂",
        serialNo: "ARM-01",
        lifecycleStatus: "DRAFT",
        sortOrder: 1,
        idempotencyKey: "component-create-key",
      }),
    ).resolves.toMatchObject({
      component: { id: "component-1", lifecycle: "ACTIVE" },
      robotEtag: '"robot-1:3"',
    });
    await expect(
      updateRobotComponent(scope, {
        componentId: "component-1",
        etag: '"robot-1:3"',
        displayName: "主机械臂 A",
        idempotencyKey: "component-update-key",
      }),
    ).resolves.toMatchObject({ component: { displayName: "主机械臂" } });
    await expect(
      transitionRobotComponentLifecycle(scope, {
        componentId: "component-1",
        etag: '"robot-1:3"',
        lifecycleStatus: "RETIRED",
        reason: "保留标定和 Channel 历史",
        idempotencyKey: "component-retire-key",
      }),
    ).resolves.toMatchObject({ topologyRevision: "topology-2" });

    const createCall = fetchMock.mock.calls[0];
    const updateCall = fetchMock.mock.calls[1];
    const transitionCall = fetchMock.mock.calls[2];
    if (!createCall || !updateCall || !transitionCall) {
      throw new Error("expected all component requests");
    }
    const [createUrl, createInit] = createCall;
    const [updateUrl, updateInit] = updateCall;
    const [transitionUrl, transitionInit] = transitionCall;
    if (!createInit || !updateInit || !transitionInit) {
      throw new Error("expected all component request metadata");
    }
    expect(createUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/robots/robot-1/components",
    );
    expect(JSON.parse(String(createInit.body))).toEqual({
      component_model_id: "arm-model-1",
      component_type: "ARM",
      display_name: "主机械臂",
      serial_no: "ARM-01",
      lifecycle_status: "DRAFT",
      sort_order: 1,
    });
    expect(new Headers(createInit.headers).get("If-Match")).toBe('"robot-1:2"');
    expect(new Headers(createInit.headers).get("Idempotency-Key")).toBe(
      "component-create-key",
    );
    expect(updateUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/components/component-1",
    );
    expect(JSON.parse(String(updateInit.body))).toEqual({
      display_name: "主机械臂 A",
    });
    expect(new Headers(updateInit.headers).get("If-Match")).toBe('"robot-1:3"');
    expect(transitionUrl).toBe(
      "/api/v1/projects/project-robotics/regions/cn-shanghai-1/components/component-1:transition",
    );
    expect(JSON.parse(String(transitionInit.body))).toEqual({
      lifecycle_status: "RETIRED",
      reason: "保留标定和 Channel 历史",
    });
    expect(new Headers(transitionInit.headers).get("Idempotency-Key")).toBe(
      "component-retire-key",
    );
  });
});
