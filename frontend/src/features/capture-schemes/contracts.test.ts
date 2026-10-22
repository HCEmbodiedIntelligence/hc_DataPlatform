import { robotConfigPath } from "./RobotProcessingLink";
import { describe, expect, it } from "vitest";
import {
  candidateFromInfo,
  validateEditor,
  mcapConfiguration,
  newFeature,
  normalizeAdapters,
} from "./editor-model";
import {
  presenceLabel,
  structureDiff,
  captureProblemLabels,
} from "./presentation";
import { safeReturnTo, schemeLibraryPath, draftKey } from "./navigation";
import { fixtureDocument } from "./fixtures.test-support";
import { documentSchema } from "./contracts";
import { buildPageCatalog } from "../../shared/routing/page-descriptor";
import { page as schemes } from "../../pages/p24-capture-schemes/routes";
import { page as models } from "../../pages/p14-robot-models/routes";
import { page as robots } from "../../pages/p15-robots/routes";
describe("shared capture contracts", () => {
  it("keeps the governance order and allocates a new page id", () => {
    expect(
      buildPageCatalog([robots, schemes, models]).navigation[0]?.items.map(
        (i) => i.label,
      ),
    ).toEqual(["机器人模型", "采集方案", "机器人实例"]);
    expect(schemes.id).toBe("P24");
  });
  it.each([
    "https://evil.test",
    "//evil.test",
    "/\\evil.test",
    "/%2fevil.test",
    "/ingest/sources\n//evil.test",
    "javascript:alert(1)",
    "/unknown",
    "/settings/robot-instances/%5cevil",
  ])("rejects unsafe return %s", (value) =>
    expect(safeReturnTo(value)).toBeNull(),
  );
  it("keeps internal query and range context", () => {
    const path = "/ingest/sources?robot.wizard=new&robot.q=X1";
    expect(safeReturnTo(path)).toBe(path);
    expect(
      new URL(schemeLibraryPath(path), "https://local").searchParams.get(
        "returnTo",
      ),
    ).toBe(path);
    expect(
      draftKey("task", {
        organizationId: "a",
        projectId: "b",
        regionCode: "global",
      }),
    ).not.toBe(
      draftKey("task", {
        organizationId: "a",
        projectId: "c",
        regionCode: "global",
      }),
    );
  });
  it("preserves 15 Hz and independent state/action axes; never guesses units", () => {
    const doc = fixtureDocument();
    expect(validateEditor(doc)).toEqual([]);
    doc.output.features.action!.units = [""];
    expect(validateEditor(doc).join()).toContain("明确单位");
    const candidate = candidateFromInfo(doc, {
      fps: 15,
      features: {
        action: { dtype: "float32", shape: [2], names: ["b", "a"] },
        timestamp: { dtype: "float32", shape: [1] },
        frame_index: { dtype: "int64", shape: [1] },
      },
    });
    expect(candidate.output.features.action?.names).toEqual(["b", "a"]);
    expect(candidate.output.features.action?.units).toEqual(["", ""]);
    expect(candidate.output.frequency_hz).toBe(15);
    expect(Object.keys(candidate.output.features)).toEqual(["action"]);
  });
  it("shows ordered names, units and required camera changes explicitly", () => {
    const before = fixtureDocument(),
      after = fixtureDocument();
    after.output.features["observation.state"]!.names = ["right", "left"];
    after.output.features.action!.units = ["mm"];
    expect(structureDiff(before, after).map((d) => d.path)).toEqual([
      "output.features.action.units",
      "output.features.observation.state.names",
    ]);
  });
  it("distinguishes absence, real zero, damaged data and annotation configuration", () => {
    expect(presenceLabel(null)).toBe("未采集");
    expect(presenceLabel(0)).toBe("0");
    expect(captureProblemLabels.CAPTURE_SOURCE_INVALID).toContain("损坏");
    expect(captureProblemLabels.ANNOTATION_PENDING).toBe("标注待配置");
  });
});

it("routes legacy raw links to shared association management and rejects external return", () => {
  expect(robotConfigPath("r1", "https://evil.test")).toBe(
    "/settings/robot-instances?robot=r1&section=processing",
  );
});

it("builds the processing terminal MCAP contract with separate layouts at 15 Hz", () => {
  const doc = fixtureDocument();
  const config = mcapConfiguration(doc);
  config.state_topic = "/state";
  config.action_topic = "/action";
  doc.adapters = [
    {
      source_format: "MCAP",
      parser_version: "native-mcap-recording/2",
      configuration: config,
    },
  ];
  expect(validateEditor(doc)).toEqual([]);
  expect(config.state_joint_names).toEqual(["left", "right"]);
  expect(config.action_joint_names).toEqual(["grip"]);
  expect(config.action_joint_units).toEqual(["m"]);
  expect(config.fps).toBe(15);
});

it("rejects internal traversal to an unregistered return target", () => {
  expect(safeReturnTo("/settings/robot-instances/../../unknown")).toBeNull();
});

it("keeps camera requirements and adapter sources aligned when definitions change", () => {
  const doc = fixtureDocument();
  const name = "observation.images.head";
  doc.output.features[name] = newFeature("rgb");
  const configuration = mcapConfiguration(doc);
  configuration.cameras[0]!.topic = "/head";
  configuration.cameras[0]!.message_type = "sensor_msgs/msg/Image";
  configuration.primary_camera = "/head";
  configuration.time_source = "RGB_LOG_TIME";
  doc.adapters.push({
    source_format: "MCAP",
    parser_version: "native-mcap-recording/2",
    configuration,
  });
  doc.output.features[name]!.required = false;
  expect(mcapConfiguration(doc).cameras[0]).toMatchObject({
    required: false,
    topic: "/head",
  });
  doc.adapters[0] = {
    source_format: "LEROBOT_V3",
    parser_version: "lerobot-v3/1",
    codebase_version: "v3.0",
    feature_sources: { [name]: "camera.head", action: "robot.action" },
  };
  delete doc.output.features[name];
  const normalized = normalizeAdapters(doc);
  expect(normalized.adapters[0]).toMatchObject({
    feature_sources: { action: "robot.action" },
  });
  expect(normalized.adapters[1]).toMatchObject({
    configuration: {
      cameras: [],
      primary_camera: null,
      collection_mode: "JOINT_ONLY",
    },
  });
  expect(validateEditor(normalized).join()).toContain("请选择状态消息记录时间");
});

it.each(["MCAP", "CAPTURE_BUNDLE"] as const)(
  "retains %s shared output and numeric sources through load, edit and save",
  (format) => {
    const doc = fixtureDocument();
    const feature = "observation.base_pose";
    doc.output.features[feature] = newFeature("numeric");
    const mapping = {
      topic: "/base/pose",
      message_type: "geometry_msgs/msg/PoseStamped",
      fields: ["pose.position.x"],
    };
    const configuration = {
      ...mcapConfiguration(doc),
      joint_names: [],
      joint_units: [],
      state_topic: "/state",
      action_topic: "/action",
      semantic_output: structuredClone(doc.output),
      numeric_sources: { [feature]: mapping },
      alignment_policy: "FIXED_GRID" as const,
    };
    doc.adapters.push(
      format === "MCAP"
        ? {
            source_format: format,
            parser_version: "native-mcap-recording/2",
            configuration,
          }
        : {
            source_format: format,
            parser_version: "capture-bundle/1",
            configuration,
          },
    );
    const loaded = documentSchema.parse(doc);
    loaded.output.frequency_hz = 30;
    loaded.output.features[feature]!.units = ["m"];
    const saved = documentSchema.parse(normalizeAdapters(loaded));
    expect(saved.adapters[1]).toMatchObject({
      configuration: {
        fps: 30,
        joint_names: [],
        joint_units: [],
        alignment_policy: "FIXED_GRID",
        numeric_sources: { [feature]: mapping },
        semantic_output: saved.output,
      },
    });
    expect(validateEditor(saved)).toEqual([]);
    delete loaded.output.features[feature];
    expect(normalizeAdapters(loaded).adapters[1]).toMatchObject({
      configuration: {
        numeric_sources: {},
        semantic_output: loaded.output,
      },
    });
  },
);
