// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { useShellStore } from "../../shared/scope/shell-store";
import RobotsPage from "./page";

const {
  bindVersion,
  createModel,
  createModelDraft,
  createRobot,
  getVersion,
  preflight,
  publishVersion,
  replaceMappings,
  useRobotBootstrap,
  uploadAssets,
} = vi.hoisted(() => ({
  bindVersion: vi.fn(),
  createModel: vi.fn(),
  createModelDraft: vi.fn(),
  createRobot: vi.fn(),
  getVersion: vi.fn(),
  preflight: vi.fn(),
  publishVersion: vi.fn(),
  replaceMappings: vi.fn(),
  useRobotBootstrap: vi.fn(),
  uploadAssets: vi.fn(),
}));

const robot = {
  id: "robot-1",
  displayName: "XR-01",
  serialNo: "XR-01-SN",
  lifecycle: "ACTIVE",
  connectivity: {
    state: "ONLINE",
    observedAt: "2026-08-21T08:00:00Z",
    source: "edge-agent",
    reasonCode: null,
  },
  effectiveModelBinding: null,
  etag: '"robot-1:2"',
  topologyRevision: "topology-1",
  allowedActions: ["VIEW", "EDIT", "TRANSITION"],
} as const;

vi.mock("../../shared/auth/use-capabilities", () => ({
  useOrganizationCapabilities: () => ({
    has: (capability: string) =>
      capability === "robot.manage" || capability === "robot_model.manage",
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../features/viewer", () => ({
  createLazyThreeRobotSceneLoader: vi.fn(() => vi.fn()),
  RobotSceneCore: () => <div data-testid="robot-model-preview">3D preview</div>,
}));

vi.mock("../../features/robots/api", () => ({
  useRobots: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: {
      items: [
        {
          id: robot.id,
          displayName: robot.displayName,
          serialNo: robot.serialNo,
          lifecycle: robot.lifecycle,
          connectivity: robot.connectivity.state,
        },
      ],
      pageInfo: {
        start_cursor: null,
        end_cursor: null,
        has_previous_page: false,
        has_next_page: false,
      },
      snapshotAt: "2026-08-21T08:00:00Z",
    },
  }),
  useRobotBootstrap,
  useCreateRobot: () => ({
    isPending: false,
    error: null,
    mutateAsync: createRobot,
  }),
}));

vi.mock("../../features/robot-models/api", () => ({
  authorizeRobotModelAssetDownload: vi.fn(),
  getRobotModelVersion: getVersion,
  useRobotModelVersion: () => ({
    data: undefined,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useRobotModelAssets: () => ({
    data: undefined,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useRobotModelJointMappings: () => ({
    data: undefined,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCreateRobotModel: () => ({
    isPending: false,
    error: null,
    mutateAsync: createModel,
  }),
  useCreateRobotModelDraft: () => ({
    isPending: false,
    error: null,
    mutateAsync: createModelDraft,
  }),
  useUploadRobotModelAssets: () => ({
    isPending: false,
    error: null,
    mutateAsync: uploadAssets,
  }),
  useReplaceRobotModelJointMappings: () => ({
    isPending: false,
    error: null,
    mutateAsync: replaceMappings,
  }),
  usePreflightRobotModelPublish: () => ({
    isPending: false,
    error: null,
    mutateAsync: preflight,
  }),
  usePublishRobotModelVersion: () => ({
    isPending: false,
    error: null,
    mutateAsync: publishVersion,
  }),
  useBindRobotModelVersion: () => ({
    isPending: false,
    error: null,
    mutateAsync: bindVersion,
  }),
}));

function browserFile(
  content: string,
  name: string,
  type: string,
  relativePath = "",
): File {
  const file = new File([content], name, { type });
  Object.defineProperties(file, {
    text: {
      configurable: true,
      value: async () => content,
    },
    arrayBuffer: {
      configurable: true,
      value: async () => new TextEncoder().encode(content).buffer,
    },
    webkitRelativePath: {
      configurable: true,
      value: relativePath,
    },
  });
  return file;
}

const urdf = `<?xml version="1.0"?>
<robot name="xr01">
  <link name="base" />
  <link name="arm" />
  <joint name="joint_1" type="revolute">
    <parent link="base" />
    <child link="arm" />
  </joint>
</robot>`;

const config = JSON.stringify({
  joint_mapping: [
    {
      source_joint_name: "telemetry_joint_1",
      target_joint_name: "joint_1",
      direction: "SAME",
    },
  ],
});

function renderPage(path = "/settings/robots") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RobotsPage />
    </MemoryRouter>,
  );
}

async function openAndParseModel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    screen.getAllByRole("button", { name: "导入 URDF / 配置" })[0]!,
  );
  await user.upload(screen.getByLabelText("选择机器人模型文件"), [
    browserFile(urdf, "robot.urdf", "application/xml"),
    browserFile(config, "robot.config.json", "application/json"),
  ]);
  await user.click(screen.getByRole("button", { name: "解析文件并预览" }));
  await screen.findByText("URDF 结构解析通过");
}

beforeEach(() => {
  useShellStore.getState().setSessionScopes(
    [
      {
        organizationId: "org-p15",
        projectId: "project-p15",
        regionCodes: ["region-p15"],
        projectWide: false,
        capabilities: ["robot.manage", "robot_model.manage"],
      },
    ],
    1,
  );
  useShellStore.getState().setScope({
    organizationId: "org-p15",
    projectId: "project-p15",
    regionCode: "region-p15",
  });
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => `blob:preview-${Math.random()}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    configurable: true,
    value: vi.fn(() => "p15-command"),
  });
  Object.defineProperty(globalThis.crypto, "subtle", {
    configurable: true,
    value: {
      digest: vi.fn(async () => new Uint8Array(32).buffer),
    },
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );

  useRobotBootstrap.mockReturnValue({
    data: robot,
    isPending: false,
    isError: false,
    refetch: vi.fn(async () => ({ data: robot })),
  });
  createRobot.mockResolvedValue(robot);
  createModel.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-1",
    versionLabel: "1.0.0",
  });
  createModelDraft.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-1",
    versionLabel: "update",
  });
  uploadAssets.mockResolvedValue("upload-1");
  getVersion.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-1",
    versionLabel: "1.0.0",
    etag: '"draft:2"',
  });
  replaceMappings.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-1",
    versionLabel: "1.0.0",
    etag: '"draft:3"',
  });
  preflight.mockResolvedValue({
    allowed: true,
    preflight_token: "preflight-token",
    expected_etag: '"draft:3"',
    checks: [],
  });
  publishVersion.mockResolvedValue({
    id: "version-published",
    robotModelId: "model-1",
    versionLabel: "1.0.0",
    etag: '"published:1"',
  });
  bindVersion.mockResolvedValue({ binding_id: "binding-1" });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P15 robot asset page", () => {
  it("loads organization robots when no project is selected", async () => {
    useShellStore.getState().setSession(null, null);
    useShellStore
      .getState()
      .setSession(
        { actorId: "robot-manager", displayName: "机器人管理员", roleIds: [] },
        "session-without-project",
      );
    useShellStore.getState().setSessionScopes(
      [],
      2,
      [],
      [
        {
          organizationId: "org-p15",
          organizationName: "P15 组织",
          memberStatus: "ACTIVE",
        },
      ],
    );

    renderPage();

    expect((await screen.findAllByText("XR-01")).length).toBeGreaterThan(0);
    expect(screen.queryByText("尚未加入组织")).not.toBeInTheDocument();
  });

  it("selects the first robot for real and removes component topology concepts", async () => {
    renderPage();

    await waitFor(() =>
      expect(useRobotBootstrap).toHaveBeenCalledWith("robot-1"),
    );
    expect(
      screen.getAllByRole("button", { name: "导入 URDF / 配置" })[0],
    ).toBeEnabled();
    expect(screen.queryByText("父组件")).not.toBeInTheDocument();
    expect(screen.queryByText("组件拓扑")).not.toBeInTheDocument();
    expect(screen.getByText("解析 → 预览 → 保存")).toBeInTheDocument();
  });

  it("parses imported URDF and configuration before showing the preview", async () => {
    const user = userEvent.setup();
    renderPage("/settings/robots?robotId=robot-1");

    await openAndParseModel(user);

    expect(screen.getByTestId("robot-model-preview")).toBeInTheDocument();
    expect(screen.getByText("robot.urdf")).toBeInTheDocument();
    expect(screen.getByDisplayValue("telemetry_joint_1")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "保存机器人模型" }),
    ).toBeEnabled();
  });

  it("writes mappings into configuration and completes save, publish and bind", async () => {
    const user = userEvent.setup();
    renderPage("/settings/robots?robotId=robot-1");
    await openAndParseModel(user);

    await user.clear(screen.getByDisplayValue("telemetry_joint_1"));
    await user.type(screen.getByLabelText("joint_1 的数据关节名"), "arm_joint");
    await user.click(screen.getByRole("button", { name: "保存机器人模型" }));

    await waitFor(() => expect(createModel).toHaveBeenCalled());
    await waitFor(() => expect(uploadAssets).toHaveBeenCalled());
    await waitFor(() => expect(replaceMappings).toHaveBeenCalled());
    await waitFor(() => expect(preflight).toHaveBeenCalled());
    await waitFor(() => expect(publishVersion).toHaveBeenCalled());
    await waitFor(() => expect(bindVersion).toHaveBeenCalled());
    await screen.findByText("机器人模型已保存");
    expect(uploadAssets).toHaveBeenCalledWith(
      expect.objectContaining({
        versionId: "version-draft",
        files: expect.arrayContaining([
          expect.objectContaining({
            role: "CONFIG",
            relativePath: "robot.config.json",
          }),
        ]),
      }),
    );
    expect(replaceMappings).toHaveBeenCalledWith(
      expect.objectContaining({
        mappings: [
          {
            source_joint_name: "arm_joint",
            target_joint_name: "joint_1",
            direction: "SAME",
          },
        ],
      }),
    );
    expect(preflight).toHaveBeenCalled();
    expect(publishVersion).toHaveBeenCalled();
    expect(bindVersion).toHaveBeenCalledWith(
      expect.objectContaining({
        versionId: "version-published",
        robotId: "robot-1",
      }),
    );
  });
});
