import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  authorizeEpisodeFile,
  loadEpisodeFiles,
  loadEpisodeOverview,
  secondsFromNs,
} from "./episode-detail";
import { episodeDetailFixture } from "./testing/episode-detail-fixture";

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
