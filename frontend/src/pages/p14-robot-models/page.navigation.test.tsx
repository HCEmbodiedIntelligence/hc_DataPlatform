// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useShellStore } from "../../shared/scope/shell-store";
import { Component } from "./page";

const apiFixtures = vi.hoisted(() => ({
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

vi.mock("../../features/robot-models/api", () => {
  const mutation = () => ({
    error: null,
    isPending: false,
    mutateAsync: vi.fn(),
    variables: undefined,
  });
  return {
    authorizeRobotModelAssetDownload: vi.fn(),
    useRobotModels: () => ({
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
    useRobotModelVersion: () => ({ data: apiFixtures.version }),
    useRobotModelAssets: () => ({
      data: [],
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useRobotModelBindings: () => ({
      data: [],
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useRobotModelJointMappings: () => ({
      data: apiFixtures.jointMappings,
      error: null,
      isPending: false,
      refetch: vi.fn(),
    }),
    useRobotModelAssetDownload: mutation,
    useUploadRobotModelAssets: mutation,
    useReplaceRobotModelJointMappings: mutation,
    useBindRobotModelVersion: mutation,
    useLoadRobotBindingTarget: mutation,
    usePreflightRobotModelPublish: mutation,
    usePublishRobotModelVersion: mutation,
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
      screen.getByRole("link", { name: "导入 URDF / 配置文件" }),
    ).toHaveAttribute("href", "/settings/robots");
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
