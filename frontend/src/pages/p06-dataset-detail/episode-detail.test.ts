// @vitest-environment node
import { createHash } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  authorizeEpisodeFile,
  downloadAnnotationMigration,
  loadEpisodeFiles,
  loadEpisodeOverview,
  secondsFromNs,
} from "../../features/datasets/episodes/episode-detail";
import {
  episodeDetailFixture,
  episodeMigrationFixture,
} from "./testing/episode-detail-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it("reads a fixed version and preserves unknown source and track states", async () => {
  const f = episodeDetailFixture();
  vi.mocked(request).mockResolvedValue(f.view);
  expect(await loadEpisodeOverview(f.scope, f.data)).toEqual(f.view);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      scope: f.scope,
      cache: "no-store",
      path: expect.stringContaining(f.data.data_version_id),
    }),
  );
  expect(secondsFromNs("1790690400123456789")).toBe("1790690400.123456789 秒");
  expect(secondsFromNs(null)).toBe("未记录");
});

it("keeps an approved historical source distinct from an unreviewed current draft", async () => {
  const f = episodeMigrationFixture();
  vi.mocked(request).mockResolvedValue(f.view);
  const value = await loadEpisodeOverview(f.scope, f.data);
  expect(value.annotation.latest_approval).toBeNull();
  expect(value.annotation.historical_migrations[0]?.source_status).toBe(
    "APPROVED",
  );
  const edited = structuredClone(f.view);
  edited.annotation.current_revision!.revision = 2;
  edited.annotation.current_revision!.content_hash = "a".repeat(64);
  vi.mocked(request).mockResolvedValue(edited);
  expect(
    (await loadEpisodeOverview(f.scope, f.data)).annotation.current_revision
      ?.revision,
  ).toBe(2);
});

it.each(["scope", "count", "schema", "review", "hash", "hidden", "duplicate"])(
  "rejects inconsistent historical mapping %s",
  async (change) => {
    const f = episodeMigrationFixture();
    const m = f.view.annotation.historical_migrations[0]!;
    if (change === "scope")
      m.source_data.identity = {
        ...m.source_data.identity,
        organization_id: "foreign",
      };
    if (change === "count") m.source_data.frame_count++;
    if (change === "schema") m.source_data.schema_snapshot_id = "foreign";
    if (change === "review")
      Object.assign(m, { review_automatically_transferred: true });
    if (change === "hash") m.target_revision_content_hash = "a".repeat(64);
    if (change === "hidden") {
      f.view.annotation.availability = "FORBIDDEN";
      f.view.annotation.current_revision = null;
      f.view.annotation.task_id = null;
    }
    if (change === "duplicate") f.view.annotation.historical_migrations.push(m);
    vi.mocked(request).mockResolvedValue(f.view);
    await expect(loadEpisodeOverview(f.scope, f.data)).rejects.toThrow();
  },
);

it("downloads unchanged mapping JSON bytes with size and hash verification", async () => {
  const f = episodeMigrationFixture();
  const content = '{ "historical": "exact bytes" }\n';
  f.mapping.document_size = Buffer.byteLength(content);
  f.mapping.document_sha256 = createHash("sha256")
    .update(content)
    .digest("hex");
  vi.mocked(request).mockResolvedValue(new Blob([content]));
  const blob = await downloadAnnotationMigration(f.scope, f.data, f.mapping);
  expect(await blob.text()).toBe(content);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      path: expect.stringContaining(
        `/annotation-migrations/${f.mapping.mapping_id}`,
      ),
      responseType: "blob",
      scope: f.scope,
      cache: "no-store",
    }),
  );
  vi.mocked(request).mockResolvedValue(
    new Blob([content.replace("bytes", "wrong")]),
  );
  await expect(
    downloadAnnotationMigration(f.scope, f.data, f.mapping),
  ).rejects.toThrow("校验失败");
  const controller = new AbortController();
  controller.abort();
  vi.mocked(request).mockResolvedValue(new Blob([content]));
  await expect(
    downloadAnnotationMigration(f.scope, f.data, f.mapping, controller.signal),
  ).rejects.toThrow();
  vi.mocked(request).mockClear();
  f.mapping.download.allowed = false;
  Object.assign(f.mapping.download, { denied_reason: "MISSING_CAPABILITY" });
  await expect(
    downloadAnnotationMigration(f.scope, f.data, f.mapping),
  ).rejects.toThrow("权限");
  expect(request).not.toHaveBeenCalled();
});
it.each(["version", "quality", "review", "selection", "scope"])(
  "rejects mismatched %s",
  async (field) => {
    const f = episodeDetailFixture(),
      view = structuredClone(f.view);
    if (field === "version")
      view.data = { ...view.data, content_hash: "f".repeat(64) };
    if (field === "quality") view.quality.data = { ...view.data, version: 999 };
    if (field === "review") view.data_review_status = "APPROVED";
    vi.mocked(request).mockResolvedValue(view);
    await expect(
      loadEpisodeOverview(
        field === "scope" ? { ...f.scope, organizationId: "foreign" } : f.scope,
        f.data,
        field === "selection" ? { annotationTaskId: "foreign" } : {},
      ),
    ).rejects.toThrow();
    if (field === "scope") expect(request).not.toHaveBeenCalled();
  },
);
it("rejects cross-version file pages and signed download identity mismatches", async () => {
  const f = episodeDetailFixture();
  vi.mocked(request).mockResolvedValue({
    ...f.view.files,
    data: { ...f.data, version: 999 },
  });
  await expect(loadEpisodeFiles(f.scope, f.data)).rejects.toThrow(/清单/);
  const file = f.view.files.items[0]!;
  const grant = {
    data: f.data,
    file_id: file.file_id,
    sha256: file.sha256,
    size: file.size,
    url: "http://127.0.0.1:49002/authorized",
    expires_seconds: 300,
  };
  vi.mocked(request).mockResolvedValue(grant);
  expect(await authorizeEpisodeFile(f.scope, f.data, file)).toEqual(grant);
  for (const changed of [
    { sha256: "c".repeat(64) },
    { url: "javascript:alert(1)" },
    { file_id: "e".repeat(64) },
  ]) {
    vi.mocked(request).mockResolvedValue({ ...grant, ...changed });
    await expect(authorizeEpisodeFile(f.scope, f.data, file)).rejects.toThrow();
  }
});
