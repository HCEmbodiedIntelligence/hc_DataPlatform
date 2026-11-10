// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { downloadPackageJob } from "../../features/exports/package-deliveries";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.unstubAllEnvs();
});
const path =
  "/hc-data-local/episode-deliveries/archive.zip?response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27%25E6%25B5%258B.zip&X-Amz-Signature=abcdef";
it("returns a usable local download after checking the exact archive identity", async () => {
  vi.stubEnv("VITE_ROBOT_PLATFORM_BASE_URL", "https://platform.example.test");
  const f = packageDeliveryFixture();
  const grant = {
    job_id: f.job.job_id,
    artifact_sha256: f.job.artifact_sha256,
    size_bytes: f.job.size_bytes,
    expires_seconds: 300,
    url: "https://platform.example.test" + path,
  };
  vi.mocked(request).mockResolvedValue(grant);
  await expect(downloadPackageJob(f.scope, f.job)).resolves.toEqual({
    ...grant,
    url: "/_recording_media/platform.example.test" + path,
  });
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: expect.stringContaining(f.job.job_id + "/download"),
      cache: "no-store",
    }),
  );
  vi.mocked(request).mockResolvedValue({
    ...grant,
    artifact_sha256: "f".repeat(64),
  });
  await expect(downloadPackageJob(f.scope, f.job)).rejects.toThrow(/身份/);
});
