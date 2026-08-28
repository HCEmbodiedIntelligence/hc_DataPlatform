// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import type { PendingRobotModelAsset } from "./model-file-selection";
import {
  buildRobotConfigurationAsset,
  mappingsCoverUrdf,
  parseRobotModelAssets,
  replaceRobotConfigurationAsset,
} from "./urdf-import";

function asset(
  content: string,
  relativePath: string,
  role: PendingRobotModelAsset["role"],
  mediaType: string,
): PendingRobotModelAsset {
  const file = new File([content], relativePath.split("/").at(-1)!, {
    type: mediaType,
  });
  Object.defineProperties(file, {
    text: { configurable: true, value: async () => content },
    arrayBuffer: {
      configurable: true,
      value: async () => new TextEncoder().encode(content).buffer,
    },
  });
  return { file, relativePath, role, mediaType };
}

const urdf = `<robot name="arm">
  <link name="base" />
  <link name="tool">
    <visual><geometry><mesh filename="meshes/tool.stl" /></geometry></visual>
  </link>
  <joint name="axis_1" type="revolute">
    <parent link="base" />
    <child link="tool" />
  </joint>
</robot>`;

describe("robot URDF import", () => {
  it("parses URDF, resolves model resources and completes missing mappings", async () => {
    const parsed = await parseRobotModelAssets([
      asset(urdf, "robot/arm.urdf", "URDF", "application/xml"),
      asset("solid mesh", "robot/meshes/tool.stl", "MESH", "model/stl"),
    ]);

    expect(parsed.robotName).toBe("arm");
    expect(parsed.linkNames).toEqual(["base", "tool"]);
    expect(parsed.actuatedJointNames).toEqual(["axis_1"]);
    expect(parsed.missingMeshReferences).toEqual([]);
    expect(parsed.mappings).toEqual([
      {
        source_joint_name: "axis_1",
        target_joint_name: "axis_1",
        direction: "SAME",
      },
    ]);
    expect(mappingsCoverUrdf(parsed.mappings, parsed.actuatedJointNames)).toBe(
      true,
    );
  });

  it("writes edited mappings into the canonical JSON description file", async () => {
    const parsed = await parseRobotModelAssets([
      asset(
        urdf.replace("meshes/tool.stl", ""),
        "arm.urdf",
        "URDF",
        "application/xml",
      ),
    ]);
    const configuration = buildRobotConfigurationAsset(
      parsed,
      [
        {
          source_joint_name: "telemetry_axis",
          target_joint_name: "axis_1",
          direction: "INVERTED",
        },
      ],
      { robotId: "robot-1", displayName: "Robot 1", serialNo: "SN-1" },
    );
    const saved = replaceRobotConfigurationAsset(
      [
        asset("{}", "old.config.json", "CONFIG", "application/json"),
        asset(urdf, "arm.urdf", "URDF", "application/xml"),
      ],
      configuration,
    );
    const document = JSON.parse(await configuration.file.text()) as {
      robot: { id: string };
      joint_mapping: unknown[];
    };

    expect(configuration.relativePath).toBe("robot.config.json");
    expect(document.robot.id).toBe("robot-1");
    expect(document.joint_mapping).toEqual([
      {
        source_joint_name: "telemetry_axis",
        target_joint_name: "axis_1",
        direction: "INVERTED",
      },
    ]);
    expect(saved.filter((item) => item.role === "CONFIG")).toEqual([
      configuration,
    ]);
  });

  it("rejects malformed URDF before preview or save", async () => {
    await expect(
      parseRobotModelAssets([
        asset("<robot>", "broken.urdf", "URDF", "application/xml"),
      ]),
    ).rejects.toThrow("URDF_XML_INVALID");
  });
});
