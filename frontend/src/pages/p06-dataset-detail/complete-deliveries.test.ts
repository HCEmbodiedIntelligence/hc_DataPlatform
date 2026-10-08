import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  authorizeCompleteDelivery,
  commandCompleteDelivery,
  listCompleteDeliveries,
  requestCompleteDelivery,
} from "../../features/datasets/episodes/complete-deliveries";
import { completeDeliveryFixture } from "./testing/complete-delivery-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it("requests a full fixed package without dropping depth and uses the supplied retry key", async () => {
  const f = completeDeliveryFixture();
  vi.mocked(request).mockResolvedValue(f.job);
  expect(
    await requestCompleteDelivery(f.scope, f.data, "ambiguous-retry"),
  ).toEqual(f.job);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      idempotencyKey: "ambiguous-retry",
      cache: "no-store",
      body: { data: [f.data], archive_format: "independent_episode_zip/1" },
    }),
  );
});
it.each(["scope", "data", "progress", "depth", "layout"])(
  "rejects an inconsistent %s",
  async (field) => {
    const f = completeDeliveryFixture(),
      changed = structuredClone(f.job);
    if (field === "data")
      changed.plan.members[0]!.data.content_hash = "d".repeat(64);
    if (field === "progress") changed.processed_files = 0;
    if (field === "layout") changed.plan.members[0]!.root = "other/episode";
    const raw =
      field === "depth"
        ? { ...changed, plan: { ...changed.plan, modalities: "rgb_only" } }
        : changed;
    vi.mocked(request).mockResolvedValue([raw]);
    await expect(
      listCompleteDeliveries(
        field === "scope" ? { ...f.scope, organizationId: "foreign" } : f.scope,
        f.data,
      ),
    ).rejects.toThrow();
    if (field === "scope") expect(request).not.toHaveBeenCalled();
  },
);
it("pins retry generation and refuses mismatched download hashes", async () => {
  const f = completeDeliveryFixture();
  const failed = {
    ...f.job,
    status: "FAILED" as const,
    phase: "failed" as const,
    error_code: "EPISODE_DELIVERY_PACKAGING_FAILED",
  };
  vi.mocked(request).mockResolvedValue({
    ...failed,
    status: "PENDING",
    phase: "queued",
    error_code: null,
    generation: 2,
  });
  await commandCompleteDelivery(f.scope, f.data, failed, "retry");
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({ body: { expected_generation: 1 } }),
  );
  vi.mocked(request).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: "d".repeat(64),
    size_bytes: 300,
    url: "http://127.0.0.1:49002/fixture",
    expires_seconds: 300,
  });
  await expect(
    authorizeCompleteDelivery(f.scope, f.data, f.job),
  ).rejects.toThrow(/身份不一致/);
});
it.each(["generation", "status"])(
  "refuses a retry response with a stale %s",
  async (field) => {
    const f = completeDeliveryFixture();
    const failed = {
      ...f.job,
      status: "FAILED" as const,
      phase: "failed" as const,
      error_code: "EPISODE_DELIVERY_PACKAGING_FAILED",
    };
    vi.mocked(request).mockResolvedValue({
      ...failed,
      generation: field === "generation" ? 1 : 2,
      ...(field === "generation"
        ? { status: "PENDING", phase: "queued", error_code: null }
        : {}),
    });
    await expect(
      commandCompleteDelivery(f.scope, f.data, failed, "retry"),
    ).rejects.toThrow(/重试状态不一致/);
  },
);
