// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourceEditorDialog } from "./SourceEditorDialog";

const { onSubmitMock } = vi.hoisted(() => ({
  onSubmitMock: vi.fn(),
}));

vi.mock("../../../features/robots/api", () => ({
  useRobots: () => ({
    data: {
      items: [
        {
          id: "robot-existing",
          displayName: "已有机器人",
          serialNo: "SN-OLD",
          lifecycle: "ACTIVE",
        },
      ],
    },
    isPending: false,
    isError: false,
  }),
}));

vi.mock("../../../features/robot-models/api", () => ({
  useRobotModels: () => ({
    data: {
      items: [
        {
          id: "model-alpha",
          displayName: "装配机器人模型",
          manufacturer: "HC",
          modelCode: "ARM-A",
          currentPublishedVersionId: "model-version-alpha",
        },
      ],
    },
    isPending: false,
    isError: false,
  }),
}));

beforeEach(() => {
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
});

describe("SourceEditorDialog robot provisioning", () => {
  it("creates a robot instance draft and requires a published model", async () => {
    const user = userEvent.setup();
    render(
      <SourceEditorDialog
        open
        mode="create"
        initialKind="ROBOT"
        pending={false}
        canProvisionRobot
        onClose={vi.fn()}
        onSubmit={onSubmitMock}
      />,
    );

    expect(
      screen.getByRole("region", { name: "机器人数据源创建流程" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("来源格式")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("格式版本")).not.toBeInTheDocument();
    expect(
      screen.getByRole("dialog", { name: "新建机器人数据源" }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText("名称"), "装配工位数据源");
    await user.type(screen.getByLabelText("实例名称"), "装配机器人 A");
    await user.type(screen.getByLabelText("真实机器人序列号"), "SN-001");

    await user.click(screen.getByLabelText("机器人模型"));
    await user.click(await screen.findByText("装配机器人模型 · HC/ARM-A"));
    await user.click(screen.getByRole("button", { name: /创\s*建/u }));

    await waitFor(() => expect(onSubmitMock).toHaveBeenCalledTimes(1));
    expect(onSubmitMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "装配工位数据源",
        sourceFormat: "MULTI_FORMAT",
        sourceFormatVersion: null,
        configuration: { kind: "ROBOT", transport: "HTTPS" },
        robotProvisioning: {
          mode: "CREATE",
          displayName: "装配机器人 A",
          serialNo: "SN-001",
          modelVersionId: "model-version-alpha",
        },
      }),
    );
  });

  it("prevents a second instance for an existing physical serial number", async () => {
    const user = userEvent.setup();
    render(
      <SourceEditorDialog
        open
        mode="create"
        pending={false}
        canProvisionRobot
        onClose={vi.fn()}
        onSubmit={onSubmitMock}
      />,
    );

    await user.type(screen.getByLabelText("名称"), "已有机器人数据源");
    await user.type(screen.getByLabelText("实例名称"), "重复实例");
    await user.type(screen.getByLabelText("真实机器人序列号"), "SN-OLD");

    expect(
      await screen.findByText(/序列号 SN-OLD 已对应机器人实例/u),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关联此实例" }));
    await user.click(screen.getByRole("button", { name: /创\s*建/u }));

    await waitFor(() => expect(onSubmitMock).toHaveBeenCalledTimes(1));
    expect(onSubmitMock).toHaveBeenCalledWith(
      expect.objectContaining({
        sourceFormat: "MULTI_FORMAT",
        sourceFormatVersion: null,
        robotProvisioning: {
          mode: "EXISTING",
          robotId: "robot-existing",
          modelVersionId: null,
        },
      }),
    );
  });
});
