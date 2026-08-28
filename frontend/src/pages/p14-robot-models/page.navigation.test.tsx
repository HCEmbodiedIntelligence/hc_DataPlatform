// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useShellStore } from "../../shared/scope/shell-store";
import { Component } from "./page";

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
        items: [],
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
    useRobotModelVersion: () => ({ data: undefined }),
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
      data: [],
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
});
