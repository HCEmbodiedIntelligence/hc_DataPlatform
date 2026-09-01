// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { inspectLocalUploadSelection } from "./upload-flow";

function folderFile(
  contents: BlobPart[],
  name: string,
  relativePath: string,
): File {
  const file = new File(contents, name);
  Object.defineProperty(file, "webkitRelativePath", {
    configurable: true,
    value: relativePath,
  });
  return file;
}

function manifest(rawSize = 8) {
  return {
    schema_version: 1,
    project_id: "project-flow",
    task_id: "task-flow",
    collection_job_id: "job-flow",
    rollout_id: "rollout-flow",
    collection_session_id: "session-flow",
    recording_request_id: "request-flow",
    data_package_id: "package-flow",
    sequence_no: 1,
    robot_id: "robot-flow",
    start_time: "2026-08-24T00:00:00Z",
    end_time: "2026-08-24T00:00:01Z",
    expected_topics: [],
    actual_topics: [],
    cameras: [],
    topics: [],
    files: [
      {
        path: "recording.mcap",
        size: rawSize,
        sha256: "a".repeat(64),
        crc64: "1",
        media_type: "application/octet-stream",
        role: "RAW_MCAP",
      },
    ],
    file_size: rawSize,
    sha256: "a".repeat(64),
    crc64: "1",
    compression: "none",
    recorder_version: "test/1",
  };
}

const baseInput = {
  sourceType: "BROWSER_MULTIPART" as const,
  browserSelectionMode: "folder" as const,
  objectStorageUri: "",
};

function lerobotInfo(overrides: Record<string, unknown> = {}) {
  return {
    codebase_version: "v3.0",
    robot_type: "unitree_g1",
    total_episodes: 2,
    fps: 30,
    data_path: "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
    video_path:
      "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    features: {
      "observation.state.ee_state": {},
      "observation.state.hand_state": {},
      "observation.state.robot_q_current": {},
      "action.ee_action": {},
      "action.hand_cmd": {},
      "action.robot_q_desired": {},
      "observation.images.head_stereo_left": {},
      "observation.images.head_stereo_right": {},
      "observation.images.wrist_left": {},
      "observation.images.wrist_right": {},
    },
    ...overrides,
  };
}

function lerobotFiles(info = lerobotInfo()): File[] {
  const root = "lerobot-source";
  return [
    folderFile([JSON.stringify(info)], "info.json", `${root}/meta/info.json`),
    folderFile(["task"], "tasks.jsonl", `${root}/meta/tasks.jsonl`),
    folderFile(
      ["episode"],
      "file-000.parquet",
      `${root}/meta/episodes/chunk-000/file-000.parquet`,
    ),
    folderFile(
      ["data"],
      "file-000.parquet",
      `${root}/data/chunk-000/file-000.parquet`,
    ),
    ...[
      "head_stereo_left",
      "head_stereo_right",
      "wrist_left",
      "wrist_right",
    ].map((camera) =>
      folderFile(
        [camera],
        "file-000.mp4",
        `${root}/videos/observation.images.${camera}/chunk-000/file-000.mp4`,
      ),
    ),
  ];
}

describe("P03 local upload selection", () => {
  it("recognizes a native LeRobot folder and keeps every original source path", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: lerobotFiles(),
    });

    expect(selection.problems).toEqual([]);
    expect(selection.units).toEqual([]);
    expect(selection.lerobot).toEqual(
      expect.objectContaining({
        format: "lerobot",
        version: "v3.0",
        episodeCount: 2,
      }),
    );
    expect(selection.lerobot?.sourceFiles.map((item) => item.path)).toContain(
      "meta/tasks.jsonl",
    );
  });

  it("ignores Hugging Face cache files and accepts the 154-episode Unitree revision", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        ...lerobotFiles(lerobotInfo({ total_episodes: 154 })),
        folderFile(
          [],
          "file-000.parquet.lock",
          "lerobot-source/.cache/huggingface/download/data/chunk-000/file-000.parquet.lock",
        ),
        folderFile(
          ["cache metadata"],
          "file-000.parquet.metadata",
          "lerobot-source/.cache/huggingface/download/data/chunk-000/file-000.parquet.metadata",
        ),
      ],
    });

    expect(selection.problems).toEqual([]);
    expect(selection.lerobot?.episodeCount).toBe(154);
    expect(
      selection.lerobot?.sourceFiles.some((item) =>
        item.path.startsWith(".cache/"),
      ),
    ).toBe(false);
  });

  it("supports up to 10000 LeRobot episodes and rejects 10001", async () => {
    const maximum = await inspectLocalUploadSelection({
      ...baseInput,
      files: lerobotFiles(lerobotInfo({ total_episodes: 10_000 })),
    });
    const overflow = await inspectLocalUploadSelection({
      ...baseInput,
      files: lerobotFiles(lerobotInfo({ total_episodes: 10_001 })),
    });

    expect(maximum.problems).toEqual([]);
    expect(maximum.lerobot?.episodeCount).toBe(10_000);
    expect(overflow.lerobot).toBeNull();
    expect(overflow.problems).toContainEqual(
      expect.objectContaining({ code: "LEROBOT_PROFILE_UNSUPPORTED" }),
    );
  });

  it("reports an unfinished resumable download separately from an invalid Raw source", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        ...lerobotFiles(),
        folderFile(
          ["partial"],
          "file-001.mp4.part",
          "lerobot-source/videos/observation.images.head_stereo_right/chunk-000/file-001.mp4.part",
        ),
      ],
    });

    expect(selection.lerobot).toBeNull();
    expect(selection.problems).toContainEqual(
      expect.objectContaining({
        code: "LEROBOT_SOURCE_INCOMPLETE",
        detail: expect.stringContaining("file-001.mp4.part"),
      }),
    );
  });

  it("uses the Hugging Face tree inventory to reject a partially downloaded revision", async () => {
    const files = lerobotFiles();
    const inventory = Object.fromEntries(
      files.map((file) => [
        file.webkitRelativePath.replace(/^lerobot-source\//u, ""),
        { size: file.size },
      ]),
    );
    inventory["README.md"] = { size: 12 };
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        ...files,
        folderFile(
          [JSON.stringify({ format_version: 1, files: inventory })],
          "lerobot-source.json",
          "lerobot-source/.cache/huggingface/trees/lerobot-source.json",
        ),
      ],
    });

    expect(selection.lerobot).toBeNull();
    expect(selection.problems).toContainEqual(
      expect.objectContaining({
        code: "LEROBOT_SOURCE_INCOMPLETE",
        detail: expect.stringContaining("README.md"),
      }),
    );
  });

  it("rejects an unsupported LeRobot profile instead of asking for a platform Manifest", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: lerobotFiles(lerobotInfo({ robot_type: "unknown_robot" })),
    });

    expect(selection.problems).toContainEqual(
      expect.objectContaining({ code: "LEROBOT_PROFILE_UNSUPPORTED" }),
    );
    expect(selection.problems).not.toContainEqual(
      expect.objectContaining({ code: "MANIFEST_FILE_MISSING" }),
    );
  });

  it("labels enumeration failures as local problems without contacting a server", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        folderFile(
          [new Uint8Array(8)],
          "recording.mcap",
          "capture/recording.mcap",
        ),
      ],
    });

    expect(selection.units).toEqual([]);
    expect(selection.problems).toContainEqual(
      expect.objectContaining({ code: "MANIFEST_FILE_MISSING" }),
    );
  });

  it("rejects malformed Manifest JSON during the browser-local check", async () => {
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        folderFile(
          ["{"],
          "rollout_manifest.json",
          "capture/rollout_manifest.json",
        ),
        folderFile(
          [new Uint8Array(8)],
          "recording.mcap",
          "capture/recording.mcap",
        ),
      ],
    });

    expect(selection.problems).toContainEqual(
      expect.objectContaining({ code: "MANIFEST_INVALID" }),
    );
  });

  it("rejects multiple Manifest files in one package directory", async () => {
    const contents = JSON.stringify(manifest());
    const selection = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        folderFile(
          [contents],
          "rollout_manifest.json",
          "capture/rollout_manifest.json",
        ),
        folderFile([contents], "manifest-v2.json", "capture/manifest-v2.json"),
        folderFile(
          [new Uint8Array(8)],
          "recording.mcap",
          "capture/recording.mcap",
        ),
      ],
    });

    expect(selection.units).toEqual([]);
    expect(selection.problems).toContainEqual(
      expect.objectContaining({ code: "MANIFEST_AMBIGUOUS" }),
    );
  });

  it("rejects a missing or size-mismatched Manifest RAW reference", async () => {
    const missing = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        folderFile(
          [JSON.stringify(manifest())],
          "rollout_manifest.json",
          "capture/rollout_manifest.json",
        ),
      ],
    });
    expect(missing.problems).toContainEqual(
      expect.objectContaining({ code: "RAW_FILE_MISSING" }),
    );

    const mismatched = await inspectLocalUploadSelection({
      ...baseInput,
      files: [
        folderFile(
          [JSON.stringify(manifest(9))],
          "rollout_manifest.json",
          "capture/rollout_manifest.json",
        ),
        folderFile(
          [new Uint8Array(8)],
          "recording.mcap",
          "capture/recording.mcap",
        ),
      ],
    });
    expect(mismatched.problems).toContainEqual(
      expect.objectContaining({ code: "RAW_FILE_SIZE_MISMATCH" }),
    );
  });
});
