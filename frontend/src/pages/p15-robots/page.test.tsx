// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RobotModelImportDialog } from "../../features/robot-models/importing/RobotModelImportDialog";
import { useRobotModelImport } from "../../features/robot-models/importing/use-robot-model-import";
import { useShellStore } from "../../shared/scope/shell-store";
import RobotsPage from "./page";

const {
  createModel,
  createModelDraft,
  discardImport,
  getVersion,
  preflight,
  publishVersion,
  replaceMappings,
  useScopedRobotModels,
  uploadAssets,
} = vi.hoisted(() => ({
  createModel: vi.fn(),
  createModelDraft: vi.fn(),
  discardImport: vi.fn(),
  getVersion: vi.fn(),
  preflight: vi.fn(),
  publishVersion: vi.fn(),
  replaceMappings: vi.fn(),
  useScopedRobotModels: vi.fn(),
  uploadAssets: vi.fn(),
}));

const model = {
  id: "model-1",
  manufacturer: "Unitree",
  modelCode: "G1-DEX1",
  displayName: "Unitree G1 Dex1",
  currentPublishedVersionId: "version-published",
} as const;

const publishedVersion = {
  id: "version-published",
  robotModelId: "model-1",
  versionLabel: "1.0.0",
  lifecycle: "PUBLISHED",
  assetAvailability: "AVAILABLE",
  publishReadiness: "READY",
  assetManifestHash: "manifest",
  validationInputHash: "validation",
  etag: '"published:1"',
  allowedActions: ["VIEW"],
  blockedReasons: [],
} as const;

const currentAssets = [
  {
    asset_id: "asset-urdf",
    relative_path: "robot.urdf",
    role: "URDF",
    media_type: "application/xml",
  },
  {
    asset_id: "asset-config",
    relative_path: "robot.config.json",
    role: "CONFIG",
    media_type: "application/json",
  },
] as const;

vi.mock("../../shared/auth/use-capabilities", () => ({
  useOrganizationCapabilities: () => ({
    has: (capability: string) => capability === "robot_model.manage",
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../features/viewer", () => ({
  createLazyThreeRobotSceneLoader: vi.fn(() => vi.fn()),
  RobotSceneCore: () => <div data-testid="robot-model-preview">3D preview</div>,
}));

vi.mock("../../features/robot-models/api/client", () => ({
  authorizeRobotModelAssetDownload: vi.fn(),
  authorizeRobotModelViewerAssets: vi.fn(async () => ({
    urdfUrl: "http://localhost/robot.urdf",
  })),
  createRobotModel: createModel,
  createRobotModelDraft: createModelDraft,
  discardRobotModelImport: discardImport,
  getRobotModelVersion: getVersion,
  preflightRobotModelPublish: preflight,
  publishRobotModelVersion: publishVersion,
  replaceRobotModelJointMappings: replaceMappings,
  uploadRobotModelAssets: uploadAssets,
}));
vi.mock("../../features/robot-models/api/hooks", () => ({
  useScopedRobotModels,
  useScopedRobotModelVersion: (
    _org: string | null,
    versionId: string | null,
  ) => ({
    data: versionId ? publishedVersion : undefined,
    isPending: false,
    isError: false,
  }),
  useScopedRobotModelAssets: (
    _organizationId: string | null,
    versionId: string | null,
  ) => ({
    data: versionId ? currentAssets : undefined,
    isPending: false,
    isError: false,
  }),
  useScopedRobotModelJointMappings: (
    _organizationId: string | null,
    versionId: string | null,
  ) => ({
    data: versionId
      ? [
          {
            source_joint_name: "joint_1",
            target_joint_name: "joint_1",
            direction: "SAME",
          },
        ]
      : undefined,
    isPending: false,
    isError: false,
  }),
}));

function browserFile(content: string, name: string, type: string): File {
  const file = new File([content], name, { type });
  Object.defineProperties(file, {
    text: { configurable: true, value: async () => content },
    arrayBuffer: {
      configurable: true,
      value: async () => new TextEncoder().encode(content).buffer,
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
  control_profile: { frequency_hz: 500, source: "user-edited" },
  joint_mapping: [
    {
      source_joint_name: "telemetry_joint_1",
      target_joint_name: "joint_1",
      direction: "SAME",
    },
  ],
});

function renderPage(path = "/settings/robots", strict = false) {
  const page = (
    <MemoryRouter initialEntries={[path]}>
      <RobotsPage />
    </MemoryRouter>
  );
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {strict ? <StrictMode>{page}</StrictMode> : page}
    </QueryClientProvider>,
  );
}

async function openAndParseModel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "导入模型" }));
  const dialog = within(screen.getByRole("dialog", { name: "导入机器人模型" }));
  await user.type(dialog.getByLabelText("制造商"), "Unitree");
  await user.type(dialog.getByLabelText("型号代码"), "G1-DEX1-V2");
  await user.type(dialog.getByLabelText("模型名称"), "Unitree G1 Dex1 V2");
  await user.upload(dialog.getByLabelText("选择机器人模型文件"), [
    browserFile(urdf, "robot.urdf", "application/xml"),
    browserFile(config, "robot.config.json", "application/json"),
  ]);
  await user.click(dialog.getByRole("button", { name: "解析文件并预览" }));
  await screen.findByText("URDF 结构解析通过");
}

beforeEach(() => {
  useShellStore
    .getState()
    .setSession(
      { actorId: "model-manager", displayName: "Model manager", roleIds: [] },
      "model-session",
    );
  useShellStore.getState().setSessionScopes(
    [
      {
        organizationId: "org-p15",
        projectId: "project-p15",
        regionCodes: ["region-p15"],
        projectWide: false,
        capabilities: ["robot_model.manage"],
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

  useScopedRobotModels.mockReturnValue({
    isPending: false,
    error: null,
    refetch: vi.fn(async () => undefined),
    data: {
      items: [model],
      pageInfo: {
        start_cursor: null,
        end_cursor: null,
        has_previous_page: false,
        has_next_page: false,
      },
      snapshotAt: "2026-08-21T08:00:00Z",
    },
  });
  createModel.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-2",
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
    robotModelId: "model-2",
    versionLabel: "1.0.0",
    etag: '"draft:2"',
  });
  replaceMappings.mockResolvedValue({
    id: "version-draft",
    robotModelId: "model-2",
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
    id: "version-published-2",
    robotModelId: "model-2",
    versionLabel: "1.0.0",
    etag: '"published:2"',
  });
  discardImport.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P15 robot model asset page", () => {
  it("lists published model resources without robot connectivity semantics", async () => {
    renderPage();

    expect(
      (await screen.findAllByText("Unitree G1 Dex1")).length,
    ).toBeGreaterThan(0);
    expect(useScopedRobotModels).toHaveBeenCalledWith(
      "org-p15",
      expect.objectContaining({ limit: 20 }),
    );
    expect(screen.queryByText("在线")).not.toBeInTheDocument();
    expect(screen.queryByText("离线")).not.toBeInTheDocument();
    expect(screen.queryByText("连接状态")).not.toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
  });

  it("keeps local preview object URLs alive through the StrictMode lifecycle probe", async () => {
    const user = userEvent.setup();
    renderPage("/settings/robots", true);

    await openAndParseModel(user);

    expect(screen.getAllByTestId("robot-model-preview").length).toBeGreaterThan(
      1,
    );
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("publishes the edited configuration as a model version without binding a robot", async () => {
    const user = userEvent.setup();
    renderPage();
    await openAndParseModel(user);

    await user.clear(screen.getByDisplayValue("telemetry_joint_1"));
    await user.type(screen.getByLabelText("joint_1 的数据关节名"), "arm_joint");
    await user.click(screen.getByRole("button", { name: "保存机器人模型" }));

    await waitFor(() => expect(publishVersion).toHaveBeenCalled());
    await screen.findByText("机器人模型已保存");
    expect(createModel).toHaveBeenCalledWith(
      "org-p15",
      expect.objectContaining({
        manufacturer: "Unitree",
        modelCode: "G1-DEX1-V2",
        displayName: "Unitree G1 Dex1 V2",
      }),
      expect.any(AbortSignal),
    );
    const files = uploadAssets.mock.calls[0]?.[2] as Array<{
      role: string;
      file: File;
    }>;
    const savedConfig = files.find((file) => file.role === "CONFIG");
    expect(savedConfig).toBeDefined();
    const savedDocument = JSON.parse(await savedConfig!.file.text()) as {
      control_profile: { source: string };
      joint_mapping: Array<{ source_joint_name: string }>;
    };
    expect(savedDocument.control_profile.source).toBe("user-edited");
    expect(savedDocument.joint_mapping[0]?.source_joint_name).toBe("arm_joint");
  });

  it("discards the draft and server files after a failed import", async () => {
    uploadAssets.mockRejectedValueOnce(new Error("upload failed"));
    const user = userEvent.setup();
    renderPage();
    await openAndParseModel(user);
    await user.click(screen.getByRole("button", { name: "保存机器人模型" }));

    await waitFor(() =>
      expect(discardImport).toHaveBeenCalledWith(
        "org-p15",
        "version-draft",
        undefined,
        expect.any(AbortSignal),
      ),
    );
    expect(
      await screen.findByText(/服务器临时文件已自动清理/u),
    ).toBeInTheDocument();
  });
});

function StandaloneImport({
  organizationId = "org-p15",
  enabled = true,
  onPublished = vi.fn(),
}: {
  organizationId?: string;
  enabled?: boolean;
  onPublished?: () => void | Promise<void>;
}) {
  const controller = useRobotModelImport({
    organizationId,
    enabled,
    onPublished,
    onFailureNotice: vi.fn(),
  });
  return (
    <>
      <button onClick={() => controller.beginImport(null, null)}>
        导入模型
      </button>
      <RobotModelImportDialog controller={controller} />
    </>
  );
}
function renderStandalone(onPublished?: () => void | Promise<void>) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <StandaloneImport onPublished={onPublished} />
    </QueryClientProvider>,
  );
}

describe("reusable robot import feature", () => {
  it("runs without a page or router and refresh failure never discards a published version", async () => {
    const onPublished = vi
      .fn()
      .mockRejectedValue(new Error("refresh unavailable"));
    renderStandalone(onPublished);
    const user = userEvent.setup();
    await openAndParseModel(user);
    await user.click(screen.getByRole("button", { name: "保存机器人模型" }));
    await screen.findByText("机器人模型已保存");
    await waitFor(() => expect(onPublished).toHaveBeenCalled());
    expect(discardImport).not.toHaveBeenCalled();
  });

  it("ignores late URDF parsing after closing and opening a fresh import", async () => {
    renderStandalone();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "导入模型" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText("制造商"), "HC");
    await user.type(dialog.getByLabelText("型号代码"), "XR");
    await user.type(dialog.getByLabelText("模型名称"), "XR model");
    const file = browserFile(urdf, "robot.urdf", "application/xml");
    let finish!: (value: string) => void;
    Object.defineProperty(file, "text", {
      value: () =>
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
    });
    await user.upload(dialog.getByLabelText("选择机器人模型文件"), file);
    await user.click(dialog.getByRole("button", { name: "解析文件并预览" }));
    await waitFor(() => expect(finish).toBeDefined());
    await user.click(dialog.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "导入模型" }));
    await act(async () => finish(urdf));
    expect(
      screen.getByRole("button", { name: "解析文件并预览" }),
    ).toBeDisabled();
    expect(screen.queryByText("URDF 结构解析通过")).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    const fresh = within(screen.getByRole("dialog"));
    await user.type(fresh.getByLabelText("制造商"), "HC");
    await user.type(fresh.getByLabelText("型号代码"), "fresh");
    await user.type(fresh.getByLabelText("模型名称"), "Fresh model");
    await user.upload(
      fresh.getByLabelText("选择机器人模型文件"),
      browserFile(urdf, "fresh.urdf", "application/xml"),
    );
    await user.click(fresh.getByRole("button", { name: "解析文件并预览" }));
    await screen.findByText("URDF 结构解析通过");
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it.each(["session", "grant", "capability"] as const)(
    "discards a local preview when its %s changes before saving",
    async (change) => {
      renderStandalone();
      const user = userEvent.setup();
      await openAndParseModel(user);
      act(() => {
        const store = useShellStore.getState();
        if (change === "session") store.setSession(null, null);
        else if (change === "grant") store.setSessionScopes([], 2);
        else store.setSessionScopes(store.sessionScopes, 2);
      });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(URL.revokeObjectURL).toHaveBeenCalled();
      expect(createModel).not.toHaveBeenCalled();
    },
  );

  it("aborts the entire save when the session is replaced between commands", async () => {
    let finish!: (value: unknown) => void;
    createModel.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderStandalone();
    const user = userEvent.setup();
    await openAndParseModel(user);
    await user.click(screen.getByRole("button", { name: "保存机器人模型" }));
    await waitFor(() => expect(createModel).toHaveBeenCalled());
    const signal = createModel.mock.calls[0]?.[2] as AbortSignal;
    useShellStore.getState().setSession(null, null);
    finish({ id: "old-draft" });
    await waitFor(() => expect(signal.aborted).toBe(true));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(uploadAssets).not.toHaveBeenCalled();
    expect(discardImport).not.toHaveBeenCalled();
  });
});
