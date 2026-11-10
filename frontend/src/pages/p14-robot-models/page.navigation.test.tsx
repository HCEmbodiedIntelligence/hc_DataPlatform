// @vitest-environment jsdom

import * as matchers from "@testing-library/jest-dom/matchers";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useShellStore } from "../../shared/scope/shell-store";
import { Component } from "./page";

vi.mock("../../features/robot-models/importing/use-robot-model-import", () => ({
  useRobotModelImport: () => ({ beginImport: vi.fn() }),
}));
vi.mock("../../features/robot-models/importing/RobotModelImportDialog", () => ({
  RobotModelImportDialog: () => null,
}));

expect.extend(matchers);

const apiFixtures = vi.hoisted(() => ({
  deleteModel: vi.fn(),
  jointMappings: [] as Array<{
    source_joint_name: string;
    target_joint_name: string;
    direction: "SAME" | "INVERTED";
  }>,
  models: [] as Array<{
    id: string;
    manufacturer: string;
    modelCode: string;
    displayName: string;
    currentPublishedVersionId: string | null;
  }>,
  version: undefined as
    | {
        id: string;
        robotModelId: string;
        versionLabel: string;
        lifecycle: "DRAFT" | "PUBLISHED";
        assetAvailability: "AVAILABLE";
        publishReadiness: "READY";
        assetManifestHash: string | null;
        validationInputHash: string | null;
        etag: string;
        allowedActions: string[];
        blockedReasons: Array<{ code: string; message: string }>;
      }
    | undefined,
}));

vi.mock("../../features/robot-models/api/hooks", () => {
  const mutation = () => ({
    error: null,
    isPending: false,
    mutateAsync: vi.fn(),
    variables: undefined,
  });
  return {
    useScopedDeleteRobotModel: () => ({
      ...mutation(),
      reset: vi.fn(),
      mutate: apiFixtures.deleteModel,
    }),
    useScopedRobotModels: () => ({
      data: {
        items: apiFixtures.models,
        pageInfo: {
          start_cursor: null,
          end_cursor: null,
          has_previous_page: false,
          has_next_page: false,
        },
        snapshotAt: "2026-08-26T02:00:00Z",
      },
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useScopedRobotModelVersion: () => ({ data: apiFixtures.version }),
    useScopedRobotModelAssets: () => ({
      data: [],
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useScopedRobotModelBindings: () => ({
      data: [],
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useScopedRobotModelJointMappings: () => ({
      data: apiFixtures.jointMappings,
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useScopedRobotModelAssetDownload: mutation,
    useScopedUploadRobotModelAssets: mutation,
    useScopedReplaceRobotModelJointMappings: mutation,
    useScopedBindRobotModelVersion: mutation,
    useScopedLoadRobotBindingTarget: mutation,
    useScopedPreflightRobotModelPublish: mutation,
    useScopedPublishRobotModelVersion: mutation,
  };
});

vi.mock("../../features/viewer", () => ({
  createLazyThreeRobotSceneLoader: vi.fn(),
  RobotSceneCore: () => null,
}));

beforeEach(() => {
  apiFixtures.jointMappings = [];
  apiFixtures.models = [];
  apiFixtures.version = undefined;
  useShellStore.getState().setScope({
    organizationId: "org-p14",
    projectId: "project-p14",
    regionCode: "region-p14",
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
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
});

afterEach(() => {
  cleanup();
  useShellStore.setState({ platformCapabilities: [], bootstrapLoaded: false });
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P14 robot model asset navigation", () => {
  it("exposes the URDF import flow from the model asset page header", () => {
    render(
      <MemoryRouter initialEntries={["/settings/robot-models"]}>
        <Component />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("button", { name: "导入模型" }),
    ).toBeInTheDocument();
  });

  it("confirms the chosen row before deleting a model", async () => {
    useShellStore.setState({
      bootstrapLoaded: true,
      platformCapabilities: ["robot_model.manage"],
    });
    apiFixtures.models = ["A", "B"].map((id) => ({
      id,
      displayName: `模型 ${id}`,
      modelCode: id,
      manufacturer: "HC",
      currentPublishedVersionId: `version-${id}`,
    }));
    render(
      <MemoryRouter>
        <Component />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^模型 B/ }));
    fireEvent.click(screen.getByRole("button", { name: "删除模型 模型 B" }));
    const dialog = await screen.findByRole("dialog", {
      name: "删除机器人模型",
    });
    expect(dialog.textContent).toContain("模型 B");
    expect(apiFixtures.deleteModel).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
    expect(apiFixtures.deleteModel).toHaveBeenCalledWith(
      "B",
      expect.any(Object),
    );
  });

  it("shows published joint mappings as read-only version facts", async () => {
    apiFixtures.models = [
      {
        id: "model-unitree",
        manufacturer: "Unitree",
        modelCode: "G1",
        displayName: "Unitree G1",
        currentPublishedVersionId: "version-unitree-1",
      },
    ];
    apiFixtures.version = {
      id: "version-unitree-1",
      robotModelId: "model-unitree",
      versionLabel: "2026.09.01",
      lifecycle: "PUBLISHED",
      assetAvailability: "AVAILABLE",
      publishReadiness: "READY",
      assetManifestHash: null,
      validationInputHash: null,
      etag: '"version-unitree-1:1"',
      allowedActions: [],
      blockedReasons: [],
    };
    apiFixtures.jointMappings = [
      {
        source_joint_name: "left_hip_pitch",
        target_joint_name: "left_hip_pitch_joint",
        direction: "SAME",
      },
    ];

    render(
      <MemoryRouter
        initialEntries={[
          "/settings/robot-models?modelId=model-unitree&versionId=version-unitree-1&detailTab=mapping",
        ]}
      >
        <Component />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("table", { name: "固定版本关节映射" }),
    ).toBeInTheDocument();
    expect(screen.getByText("left_hip_pitch")).toBeInTheDocument();
    expect(screen.getByText("left_hip_pitch_joint")).toBeInTheDocument();
    expect(
      screen.getByText(/已发布版本的关节映射仅可查看/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "保存映射" }),
    ).not.toBeInTheDocument();
  });
});
