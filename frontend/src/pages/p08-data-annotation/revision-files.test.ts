// @vitest-environment node
import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  downloadRevisionFile,
  loadRevisionFile,
  type RevisionFile,
} from "./revision-files";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const transport = vi.mocked(request);
afterEach(() => vi.clearAllMocks());

function fixture() {
  const revision = createVisualAnnotationBundle({
    mode: "annotation",
  }).history.revisions.at(-1)!;
  const content = '{ "exact": "bytes" }\n';
  const file: RevisionFile = {
    task_id: revision.task_id,
    revision: revision.revision,
    revision_content_hash: revision.content_hash,
    format_version: "hc-annotation/1",
    status: "READY",
    generation: 1,
    attempts: 1,
    error_code: null,
    updated_at: "2026-09-29T00:00:00Z",
    content_sha256: createHash("sha256").update(content).digest("hex"),
    size_bytes: Buffer.byteLength(content),
  };
  return { revision, file, content };
}

describe("annotation revision file integrity", () => {
  it("rejects file status from another saved content hash", async () => {
    const { revision, file } = fixture();
    transport.mockResolvedValue({
      ...file,
      revision_content_hash: "b".repeat(64),
    });
    await expect(
      loadRevisionFile(visualAnnotationScope, revision),
    ).rejects.toThrow("不一致");
  });
  it("checks exact downloaded bytes and binds the revision in the request", async () => {
    const { revision, file, content } = fixture();
    transport.mockResolvedValue(new Blob([content]));
    const blob = await downloadRevisionFile(
      visualAnnotationScope,
      revision,
      file,
      true,
    );
    expect(await blob.text()).toBe(content);
    expect(transport).toHaveBeenCalledWith(
      expect.objectContaining({
        responseType: "blob",
        scope: visualAnnotationScope,
        query: { revision: revision.revision, allow_unreviewed: true },
      }),
    );
  });
  it("rejects a truncated or corrupted file", async () => {
    const { revision, file } = fixture();
    transport.mockResolvedValue(new Blob(["corrupt"]));
    await expect(
      downloadRevisionFile(visualAnnotationScope, revision, file, true),
    ).rejects.toThrow("校验失败");
  });
  it("checks cancellation again after hashing, before saving a browser download", async () => {
    const { revision, file, content } = fixture();
    transport.mockResolvedValue(new Blob([content]));
    const controller = new AbortController();
    controller.abort();
    await expect(
      downloadRevisionFile(
        visualAnnotationScope,
        revision,
        file,
        true,
        controller.signal,
      ),
    ).rejects.toThrow();
  });
});
