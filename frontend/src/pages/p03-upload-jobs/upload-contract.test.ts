// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import {
  discoverFolderUploadBundles,
  MAX_MANIFEST_BYTES,
  MAX_PACKAGE_BYTES,
  parseManifestFile,
  partBounds,
  planUploadParts,
  uploadProblemCopy,
  validateObjectStorageUri,
} from "./upload-contract";

function folderFile(
  contents: BlobPart[],
  name: string,
  relativePath: string,
  options?: FilePropertyBag,
): File {
  const file = new File(contents, name, options);
  Object.defineProperty(file, "webkitRelativePath", {
    configurable: true,
    value: relativePath,
  });
  return file;
}

function folderManifest(
  dataPackageId: string,
  rawPath = "recording.mcap",
  sourceEpisode?: number,
) {
  return JSON.stringify({
    project_id: "project-folder",
    task_id: "task-folder",
    collection_job_id: "job-folder",
    rollout_id: `rollout-${dataPackageId}`,
    collection_session_id: `session-${dataPackageId}`,
    recording_request_id: `request-${dataPackageId}`,
    data_package_id: dataPackageId,
    sequence_no: 1,
    robot_id: "robot-folder",
    start_time: "2026-08-21T00:00:00Z",
    end_time: "2026-08-21T00:00:01Z",
    expected_topics: [],
    actual_topics: [],
    cameras: [],
    topics: [],
    files: [
      {
        path: rawPath,
        size: 1,
        sha256: "a".repeat(64),
        crc64: "1",
        role: "RAW_MCAP",
      },
    ],
    file_size: 1,
    sha256: "a".repeat(64),
    crc64: "1",
    compression: "none",
    recorder_version: "folder-test",
    ...(sourceEpisode === undefined
      ? {}
      : {
          source_recording: {
            kind: "HUGGING_FACE_EPISODE",
            repository: "aractingi/droid_100",
            resolved_revision: "e86f5657cac0cd48c509543e4c14c6a31352b0cc",
            episode_index: sourceEpisode,
          },
        }),
  });
}

describe("P03 formal upload contract helpers", () => {
  it("plans every supported package into bounded multipart numbers", () => {
    const small = planUploadParts(5 * 1024 ** 2 + 1);
    expect(small.partCount).toBe(2);
    expect(small.partNumbers).toEqual([1, 2]);
    expect(partBounds(2, small, 5 * 1024 ** 2 + 1)).toEqual({
      start: 5 * 1024 ** 2,
      end: 5 * 1024 ** 2 + 1,
    });

    const maximum = planUploadParts(MAX_PACKAGE_BYTES);
    expect(maximum.partCount).toBeLessThanOrEqual(10_000);
    expect(maximum.partNumbers.at(-1)).toBe(maximum.partCount);
  });

  it("rejects an empty or oversized Manifest before sending it", async () => {
    await expect(
      parseManifestFile(new File([], "rollout_manifest.json")),
    ).rejects.toMatchObject({ code: "MANIFEST_EMPTY" });
    await expect(
      parseManifestFile(
        new File(
          [new Uint8Array(MAX_MANIFEST_BYTES + 1)],
          "rollout_manifest.json",
        ),
      ),
    ).rejects.toMatchObject({ code: "MANIFEST_TOO_LARGE" });
  });

  it("preserves uint64 CRC64 values as decimal strings across JSON round trips", async () => {
    const crc64 = "13305216265320122395";
    const manifest = await parseManifestFile(
      new File(
        [JSON.stringify({ crc64, files: [{ crc64 }] })],
        "rollout_manifest.json",
        { type: "application/json" },
      ),
    );

    expect(manifest.crc64).toBe(crc64);
    expect(manifest.files[0]?.crc64).toBe(crc64);
    expect(JSON.parse(JSON.stringify(manifest))).toMatchObject({
      crc64,
      files: [{ crc64 }],
    });
  });

  it("rejects numeric CRC64 tokens before JavaScript can silently round them", async () => {
    const unsafeNumericManifest =
      '{"crc64":13305216265320122395,"files":[{"crc64":13305216265320122395}]}';

    await expect(
      parseManifestFile(
        new File([unsafeNumericManifest], "rollout_manifest.json", {
          type: "application/json",
        }),
      ),
    ).rejects.toMatchObject({
      code: "MANIFEST_INVALID",
      message: expect.stringContaining("uint64 十进制字符串"),
    });
  });

  it("keeps every checked-in generated CRC64 field string-typed", async () => {
    type CurrentManifest =
      import("../../shared/api/generated/platform").components["schemas"]["RolloutManifestV1"];
    type CurrentPart =
      import("../../shared/api/generated/platform").components["schemas"]["UploadPart"];
    type CurrentSession =
      import("../../shared/api/generated/platform").components["schemas"]["UploadSession"];

    const manifest: CurrentManifest["crc64"] = "18446744073709551615";
    const part: Exclude<CurrentPart["crc64"], null | undefined> =
      "18446744073709551615";
    const session: CurrentSession["expected_crc64"] = "18446744073709551615";

    expect([manifest, part, session]).toEqual([
      "18446744073709551615",
      "18446744073709551615",
      "18446744073709551615",
    ]);
  });

  it("does not accept object addresses carrying credentials or signed queries", () => {
    expect(
      validateObjectStorageUri(
        "s3://managed-bucket/raw/v1/project=a/recording.mcap",
      ),
    ).toBeNull();
    expect(validateObjectStorageUri("https://example.com/raw.mcap")).toContain(
      "s3://",
    );
    expect(
      validateObjectStorageUri("s3://user:secret@bucket/raw.mcap"),
    ).toContain("凭据");
    expect(
      validateObjectStorageUri("oss://bucket/raw.mcap?Signature=secret"),
    ).toContain("签名参数");
  });

  it("discovers independent packages in arbitrarily nested folders without cross-pairing names", async () => {
    const result = await discoverFolderUploadBundles([
      folderFile(
        [folderManifest("package-a")],
        "rollout_manifest.json",
        "factory/shift-1/robot-a/rollout_manifest.json",
        { type: "application/json" },
      ),
      folderFile(
        ["a"],
        "recording.mcap",
        "factory/shift-1/robot-a/recording.mcap",
      ),
      folderFile(
        [folderManifest("package-b", "captures/recording.mcap")],
        "rollout_manifest.json",
        "factory/shift-2/robot-b/meta/rollout_manifest.json",
        { type: "application/json" },
      ),
      folderFile(
        ["b"],
        "recording.mcap",
        "factory/shift-2/robot-b/meta/captures/recording.mcap",
      ),
    ]);

    expect(result.failures).toEqual([]);
    expect(result.bundles).toHaveLength(2);
    expect(result.bundles.map((bundle) => bundle.relativeDirectory)).toEqual([
      "factory/shift-1/robot-a",
      "factory/shift-2/robot-b/meta",
    ]);
    expect(
      result.bundles.map((bundle) => bundle.rawFile.webkitRelativePath),
    ).toEqual([
      "factory/shift-1/robot-a/recording.mcap",
      "factory/shift-2/robot-b/meta/captures/recording.mcap",
    ]);
  });

  it("never uses an identically named raw file from another nested package", async () => {
    const result = await discoverFolderUploadBundles([
      folderFile(
        [folderManifest("package-a")],
        "rollout_manifest.json",
        "factory/a/rollout_manifest.json",
      ),
      folderFile(
        ["other package"],
        "recording.mcap",
        "factory/b/recording.mcap",
      ),
    ]);

    expect(result.bundles).toEqual([]);
    expect(result.failures).toEqual([
      expect.objectContaining({
        relativePath: "factory/a/rollout_manifest.json",
        code: "RAW_FILE_MISSING",
      }),
    ]);
  });

  it("rejects two converter artifacts for the same source episode in one selection", async () => {
    const result = await discoverFolderUploadBundles([
      folderFile(
        [folderManifest("package-converter-v1", "recording.mcap", 0)],
        "rollout_manifest.json",
        "factory/v1/rollout_manifest.json",
      ),
      folderFile(["v1"], "recording.mcap", "factory/v1/recording.mcap"),
      folderFile(
        [folderManifest("package-converter-v2", "recording.mcap", 0)],
        "rollout_manifest.json",
        "factory/v2/rollout_manifest.json",
      ),
      folderFile(["v2"], "recording.mcap", "factory/v2/recording.mcap"),
    ]);

    expect(result.bundles).toHaveLength(1);
    expect(result.failures).toEqual([
      expect.objectContaining({
        code: "SOURCE_RECORDING_DUPLICATE",
        detail: expect.stringContaining("package-converter-v1"),
      }),
    ]);
  });

  it("rejects an attempted parent-directory RAW_MCAP declaration before pairing files", async () => {
    const result = await discoverFolderUploadBundles([
      folderFile(
        [folderManifest("package-a", "../other/recording.mcap")],
        "rollout_manifest.json",
        "factory/a/rollout_manifest.json",
      ),
      folderFile(
        ["not eligible"],
        "recording.mcap",
        "factory/other/recording.mcap",
      ),
    ]);

    expect(result.bundles).toEqual([]);
    expect(result.failures).toEqual([
      expect.objectContaining({ code: "RAW_FILE_PATH_INVALID" }),
    ]);
  });

  it.each([
    [403, "FORBIDDEN", "当前授权不能上传"],
    [409, "VERSION_CONFLICT", "数据包事实发生冲突"],
    [429, "RATE_LIMITED", "请求频率受限"],
  ] as const)(
    "keeps HTTP %s failures visible without a Mock fallback",
    (status, code, title) => {
      const problem = uploadProblemCopy(
        createDomainError({
          code,
          problemCode: `HTTP_${status}`,
          message: `real api ${status}`,
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: `req-${status}`,
          retryable: status === 429,
          httpStatus: status,
        }),
      );
      expect(problem.title).toBe(title);
      expect(problem.detail).toBe(`real api ${status}`);
      expect(problem.requestId).toBe(`req-${status}`);
    },
  );
});
