import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { loadEpisodeQuality } from "./episode-quality";
import { qualityFixture } from "./testing/quality-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it.each(["version", "report", "rule", "legacy-pass", "scope"])(
  "rejects inconsistent %s",
  async (field) => {
    const f = qualityFixture();
    if (field === "version") f.quality.data = { ...f.data, version: 99 };
    if (field === "report") f.quality.evidence!.report_sha256 = "f".repeat(64);
    if (field === "rule") f.quality.rules[0]!.status = "pass";
    if (field === "legacy-pass") {
      f.quality.evidence = null;
      f.quality.evidence_status = "LEGACY_MISSING";
      f.quality.rules[0]!.status = "pass";
    }
    vi.mocked(request).mockResolvedValue(f.quality);
    await expect(
      loadEpisodeQuality(
        field === "scope" ? { ...f.scope, organizationId: "foreign" } : f.scope,
        f.data,
      ),
    ).rejects.toThrow();
  },
);
it("retains PASS alongside missing historical execution evidence", async () => {
  const f = qualityFixture();
  f.quality.evidence = null;
  f.quality.evidence_status = "LEGACY_MISSING";
  vi.mocked(request).mockResolvedValue(f.quality);
  const result = await loadEpisodeQuality(f.scope, f.data);
  expect(result.status).toBe("PASS");
  expect(result.rules[0]!.status).toBe("skip");
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      scope: f.scope,
      cache: "no-store",
      path: expect.stringContaining(f.data.data_version_id),
    }),
  );
});
