// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DefinitionFields } from "./DefinitionFields";
import { AdapterFields } from "./AdapterFields";
import {
  candidateFromInfo,
  validateEditor,
  normalizeAdapters,
  emptyDocument,
} from "./editor-model";
import { fixtureDocument } from "./fixtures.test-support";
import { documentSchema, type SchemeDocument } from "./contracts";

beforeEach(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener() {},
      removeListener() {},
    })),
  });
  Element.prototype.scrollTo = vi.fn();
});
afterEach(cleanup);

it("starts with all formats and edits shared ROS sources only once", async () => {
  expect(emptyDocument().input_mode).toBe("AUTO");
  expect(emptyDocument().adapters).toHaveLength(3);
  const user = userEvent.setup();
  let current = normalizeAdapters({ ...fixtureDocument(), input_mode: "AUTO" });
  function Editor() {
    const [doc, set] = useState(current);
    return (
      <AdapterFields
        document={doc}
        onChange={(next) => {
          current = next;
          set(next);
        }}
      />
    );
  }
  render(<Editor />);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(
    screen.getByText("选择一次方案即可上传，无需分别配置三种格式。"),
  ).toBeVisible();
  expect(screen.getByLabelText("MCAP 状态话题")).not.toBeVisible();
  await user.click(screen.getByText("高级来源设置（通常无需修改）"));
  await user.clear(screen.getByLabelText("MCAP 状态话题"));
  await user.type(screen.getByLabelText("MCAP 状态话题"), "/custom/state");
  expect(
    screen.queryByLabelText("CAPTURE_BUNDLE 状态话题"),
  ).not.toBeInTheDocument();
  const inputs = current.adapters.filter(
    (a) => a.source_format !== "LEROBOT_V3",
  );
  expect(inputs.map((a) => a.configuration.state_topic)).toEqual([
    "/custom/state",
    "/custom/state",
  ]);
});

it("selects all three original formats and edits bundle topics independently", async () => {
  const user = userEvent.setup();
  let current = fixtureDocument();
  function Editor() {
    const [doc, set] = useState(current);
    return (
      <AdapterFields
        document={doc}
        onChange={(next) => {
          current = next;
          set(next);
        }}
      />
    );
  }
  render(<Editor />);
  await user.click(screen.getByLabelText("MCAP（图像在话题文件内）"));
  await user.click(screen.getByLabelText("MCAP＋独立 MP4（原始包）"));
  await user.type(screen.getByLabelText("MCAP 状态话题"), "/raw/state");
  await user.type(
    screen.getByLabelText("CAPTURE_BUNDLE 状态话题"),
    "/bundle/state",
  );
  expect(documentSchema.safeParse(current).success).toBe(true);
  expect(current.adapters).toHaveLength(3);
  expect(
    current.adapters.find((a) => a.source_format === "MCAP"),
  ).toMatchObject({ configuration: { state_topic: "/raw/state" } });
  expect(
    current.adapters.find((a) => a.source_format === "CAPTURE_BUNDLE"),
  ).toMatchObject({ configuration: { state_topic: "/bundle/state" } });
});

it("uses same-name LeRobot fields by default and reveals optional source mappings", async () => {
  const user = userEvent.setup();
  let current = fixtureDocument();
  function Editor() {
    const [doc, set] = useState(current);
    return (
      <AdapterFields
        document={doc}
        onChange={(next) => {
          current = next;
          set(next);
        }}
      />
    );
  }
  render(<Editor />);
  expect(
    screen.getByRole("button", { name: "LeRobot 字段说明" }),
  ).toBeVisible();
  const source = screen.getByLabelText("observation.state LeRobot 来源");
  expect(source).not.toBeVisible();
  await user.click(screen.getByText("字段名称不同？设置对应关系"));
  expect(source).toBeVisible();
  await user.type(source, "robot.joints");
  expect(current.adapters[0]).toMatchObject({
    feature_sources: { "observation.state": "robot.joints" },
  });
  expect(screen.getByText("1 项自定义")).toBeVisible();
  await user.clear(source);
  expect(current.adapters[0]).toMatchObject({ feature_sources: {} });
});

it("moves a joint with its unit and explicitly applies a unit to the whole group", async () => {
  const user = userEvent.setup();
  let current = fixtureDocument();
  current.output.features["observation.state"]!.units = ["rad", "m"];
  function Editor() {
    const [doc, set] = useState(current);
    return (
      <DefinitionFields
        document={doc}
        onChange={(next) => {
          current = next;
          set(next);
        }}
      />
    );
  }
  render(<Editor />);
  await user.click(screen.getByText("查看与编辑 2 个关节（按记录顺序）"));
  await user.click(screen.getByLabelText("observation.state 第2轴上移"));
  expect(current.output.features["observation.state"]).toMatchObject({
    names: ["right", "left"],
    units: ["m", "rad"],
  });
  fireEvent.mouseDown(screen.getByLabelText("observation.state 统一单位"));
  await user.click(screen.getByText("角度（deg）"));
  expect(current.output.features["observation.state"]!.units).toEqual([
    "deg",
    "deg",
  ]);
  expect(current.output.features.action!.units).toEqual(["m"]);
  expect(validateEditor(current)).toEqual([]);
});

it("imports real dimension order, preserves validity and requires units before saving", async () => {
  let current: SchemeDocument = candidateFromInfo(
    { ...fixtureDocument(), software: "" },
    {
      fps: 15,
      features: {
        action: { dtype: "float32", shape: [2], names: ["right", "left"] },
        "observation.images.head": {
          dtype: "video",
          shape: [3, 400, 640],
          names: ["channels", "height", "width"],
          info: {
            "video.width": 640,
            "video.height": 400,
            "video.channels": 3,
          },
        },
        "observation.source_valid": {
          dtype: "float32",
          shape: [1],
          names: ["action"],
        },
        timestamp: { dtype: "float32", shape: [1], names: null },
      },
    },
  );
  expect(current.output.features.action!.units).toEqual(["", ""]);
  expect(validateEditor(current).join()).toContain("明确单位");
  expect(current.output.features["observation.source_valid"]!.role).toBe(
    "validity",
  );
  expect(current.output.features.timestamp).toBeUndefined();
  function Editor() {
    const [doc, set] = useState(current);
    return (
      <DefinitionFields
        document={doc}
        onChange={(next) => {
          current = next;
          set(next);
        }}
      />
    );
  }
  render(<Editor />);
  fireEvent.change(
    screen.getByLabelText("observation.images.head 图像宽度（像素）"),
    { target: { value: "800" } },
  );
  expect(current.output.features["observation.images.head"]).toMatchObject({
    shape: [3, 400, 800],
    media: { "video.width": 800, "video.height": 400 },
  });
  fireEvent.mouseDown(screen.getByLabelText("action 统一单位"));
  await userEvent.click(screen.getByText("弧度（rad）"));
  expect(documentSchema.parse(current).software).toBe("");
  expect(validateEditor(current)).toEqual([]);
});

it("shows published fields without allowing edits or reordering", async () => {
  const changed = vi.fn();
  render(
    <DefinitionFields
      document={fixtureDocument()}
      disabled
      onChange={changed}
    />,
  );
  expect(screen.queryByLabelText("实际原始帧率")).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("查看 2 个关节（按记录顺序）"));
  expect(screen.getByLabelText("observation.state 第1轴名称")).toBeDisabled();
  expect(screen.getByLabelText("observation.state 第2轴上移")).toBeDisabled();
  expect(
    screen.queryByLabelText("observation.state 统一单位"),
  ).not.toBeInTheDocument();
  expect(changed).not.toHaveBeenCalled();
});
