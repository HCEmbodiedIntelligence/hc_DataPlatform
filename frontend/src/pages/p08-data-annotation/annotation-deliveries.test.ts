import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  authorizeAnnotationDelivery,
  listAnnotationDeliveries,
} from "../../features/annotation/runtime/annotation-deliveries";
import { deliveryFixture } from "./testing/delivery-fixture";
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it.each(["scope", "revision", "data", "ready receipt"])(
  "rejects a mismatched %s",
  async (field) => {
    const f = deliveryFixture(),
      job = structuredClone(f.job);
    if (field === "scope")
      job.plan.binding.data.identity.organization_id = "other";
    if (field === "revision")
      job.plan.binding.revision_content_hash = "9".repeat(64);
    if (field === "data")
      job.plan.binding.data.data_version_id = "dv_" + "9".repeat(64);
    if (field === "ready receipt") job.artifact_sha256 = null;
    vi.mocked(request).mockResolvedValue([job]);
    await expect(listAnnotationDeliveries(f.scope, f.history)).rejects.toThrow(
      /不一致/,
    );
  },
);
it.each([
  "javascript:alert(1)",
  "https://user:password@example.test/a",
  "https://example.test/wrong",
])("rejects unsafe or mismatched download authorization: %s", async (url) => {
  const f = deliveryFixture();
  vi.mocked(request).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: url.endsWith("wrong")
      ? "9".repeat(64)
      : f.job.artifact_sha256,
    size_bytes: f.job.size_bytes,
    url,
    expires_seconds: 300,
  });
  await expect(
    authorizeAnnotationDelivery(f.scope, f.history, f.job),
  ).rejects.toThrow();
});
it("reauthorizes one immutable artifact using the current scope", async () => {
  const f = deliveryFixture();
  vi.mocked(request).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: f.job.artifact_sha256,
    size_bytes: f.job.size_bytes,
    url: "https://objects.example.test/authorized",
    expires_seconds: 300,
  });
  const grant = await authorizeAnnotationDelivery(f.scope, f.history, f.job);
  expect(grant.artifact_sha256).toBe(f.job.artifact_sha256);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      scope: f.scope,
      cache: "no-store",
    }),
  );
});
