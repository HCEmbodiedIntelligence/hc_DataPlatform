// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import {
  classifyRobotModelFiles,
  modelAssetSelectionError,
  robotModelFilesFromDrop,
} from "./model-file-selection";

describe("robot model file selection", () => {
  it("classifies a selected folder and preserves its relative paths", () => {
    const urdf = new File(["urdf"], "robot.urdf", { type: "application/xml" });
    const mesh = new File(["mesh"], "base.stl", { type: "model/stl" });

    const selection = classifyRobotModelFiles([
      { file: urdf, relativePath: "xr-01/robot.urdf" },
      { file: mesh, relativePath: "xr-01/meshes/base.stl" },
    ]);

    expect(selection.rejectedPaths).toEqual([]);
    expect(selection.assets).toEqual([
      expect.objectContaining({
        relativePath: "xr-01/robot.urdf",
        role: "URDF",
      }),
      expect.objectContaining({
        relativePath: "xr-01/meshes/base.stl",
        role: "MESH",
      }),
    ]);
    expect(modelAssetSelectionError(selection.assets)).toBeNull();
  });

  it("reads a dropped directory recursively", async () => {
    const urdf = new File(["urdf"], "robot.urdf", { type: "application/xml" });
    let rootReadCount = 0;
    const rootEntry = {
      isFile: false,
      isDirectory: true,
      name: "xr-01",
      createReader: () => ({
        readEntries: (callback: (entries: readonly unknown[]) => void) => {
          callback(
            rootReadCount++ === 0
              ? [
                  {
                    isFile: true,
                    isDirectory: false,
                    name: "robot.urdf",
                    file: (fileCallback: (file: File) => void) =>
                      fileCallback(urdf),
                  },
                ]
              : [],
          );
        },
      }),
    };
    const dataTransfer = {
      items: [
        {
          kind: "file",
          webkitGetAsEntry: () => rootEntry,
        },
      ],
      files: [],
    } as unknown as DataTransfer;

    await expect(robotModelFilesFromDrop(dataTransfer)).resolves.toEqual([
      { file: urdf, relativePath: "xr-01/robot.urdf" },
    ]);
  });

  it("rejects unsafe, empty, unsupported, or ambiguous URDF selections", () => {
    const empty = new File([], "empty.urdf");
    const unsupported = new File(["x"], "script.exe");
    const unsafe = new File(["x"], "robot.urdf");
    const validOne = new File(["one"], "one.urdf");
    const validTwo = new File(["two"], "two.xml");

    const rejected = classifyRobotModelFiles([
      { file: empty },
      { file: unsupported },
      { file: unsafe, relativePath: "../robot.urdf" },
    ]);
    expect(rejected.assets).toEqual([]);
    expect(rejected.rejectedPaths).toHaveLength(3);

    const ambiguous = classifyRobotModelFiles([
      { file: validOne },
      { file: validTwo },
    ]);
    expect(modelAssetSelectionError(ambiguous.assets)).toContain("多个 URDF");
  });
});
