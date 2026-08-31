// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import type { LeRobotFolderSelection } from "./upload-contract";
import {
  buildLeRobotImportManifest,
  LEROBOT_MULTIPART_BYTES,
} from "./lerobot-client";

describe("native LeRobot upload contract", () => {
  it("declares original files and never invents an MCAP Raw", () => {
    const info = {
      codebase_version: "v3.0",
      robot_type: "unitree_g1",
      total_episodes: 1,
    };
    const video = new File(["video"], "file.mp4");
    Object.defineProperty(video, "size", {
      value: LEROBOT_MULTIPART_BYTES + 1,
    });
    const sourceFiles = [
      {
        file: new File([JSON.stringify(info)], "info.json"),
        path: "meta/info.json",
      },
      { file: new File(["meta"], "tasks.jsonl"), path: "meta/tasks.jsonl" },
      { file: video, path: "videos/camera/chunk-000/file-000.mp4" },
    ];
    const selection: LeRobotFolderSelection = {
      format: "lerobot",
      version: "v3.0",
      robotType: "unitree_g1",
      rootDirectory: "source",
      info,
      episodeCount: 1,
      sourceFiles,
      sourceBytes: sourceFiles.reduce(
        (total, item) => total + item.file.size,
        0,
      ),
    };

    const manifest = buildLeRobotImportManifest(selection, {
      datasetId: "dataset-a",
      collectionTaskId: "task-a",
      robotId: "robot-a",
    });

    expect(manifest.files.map((item) => item.path)).toEqual(
      sourceFiles.map((item) => item.path),
    );
    expect(manifest.files[2]?.part_count).toBe(2);
    expect(manifest.files.some((item) => item.path.endsWith(".mcap"))).toBe(
      false,
    );
  });
});
