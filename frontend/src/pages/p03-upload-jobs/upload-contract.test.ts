// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import {
  MAX_MANIFEST_BYTES,
  MAX_PACKAGE_BYTES,
  parseManifestFile,
  partBounds,
  planUploadParts,
  uploadProblemCopy,
  validateObjectStorageUri,
} from "./upload-contract";

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
