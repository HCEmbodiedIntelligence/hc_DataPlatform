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

describe("P03 local upload selection", () => {
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
        folderFile(
          [contents],
          "manifest-v2.json",
          "capture/manifest-v2.json",
        ),
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
