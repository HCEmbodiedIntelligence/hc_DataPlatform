import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  checkedPackageJob,
  createPackageBatch,
  downloadPackageJob,
  loadPackageCandidates,
  loadCollectionAnnotationHistory,
} from "../../features/exports/package-deliveries";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it("pins annotation revision and explicit depth choice in the queued request", async () => {
  const f = packageDeliveryFixture(true),
    a = f.annotation.job.plan.binding;
  const members = [
    {
      data: f.data,
      annotation: {
        task_id: a.task_id,
        revision: a.revision,
        track: a.track,
        allow_unreviewed: true,
      },
    },
  ];
  vi.mocked(request).mockResolvedValue(f.job);
  expect(
    await createPackageBatch(f.scope, members, "rgbd", "retry-key"),
  ).toEqual(f.job);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      path: expect.stringMatching(/\/collections$/),
      idempotencyKey: "retry-key",
      body: { members, modalities: "rgbd" },
    }),
  );
  const wrong = structuredClone(f.job);
  wrong.plan.members[0]!.annotation!.binding.revision++;
  vi.mocked(request).mockResolvedValue(wrong);
  await expect(
    createPackageBatch(f.scope, members, "rgbd", "retry-key"),
  ).rejects.toThrow(/标注/);
});
it.each([
  "foreign tenant",
  "depth exclusion",
  "annotation parent",
  "missing totals",
  "progress",
  "duplicate Episode",
])("refuses inconsistent %s in a cached response", (field) => {
  const f = packageDeliveryFixture(true),
    job = structuredClone(f.job);
  if (field === "foreign tenant")
    job.plan.members[0]!.data.identity.organization_id = "foreign";
  if (field === "depth exclusion")
    job.plan.members[0]!.excluded_depth_features = ["depth"];
  if (field === "annotation parent")
    job.plan.members[0]!.annotation!.binding.data.version++;
  if (field === "missing totals") job.total_bytes = null;
  if (field === "progress") job.processed_files = 0;
  if (field === "duplicate Episode")
    job.plan.members.push({
      ...job.plan.members[0]!,
      data: {
        ...job.plan.members[0]!.data,
        data_version_id: "dv_" + "9".repeat(64),
      },
    });
  expect(() => checkedPackageJob(job, f.scope)).toThrow();
});
it("allows unknown size while generating and rejects a changed fixed candidate snapshot", async () => {
  const f = packageDeliveryFixture();
  expect(
    checkedPackageJob(
      {
        ...f.job,
        status: "RUNNING",
        phase: "projecting",
        total_files: null,
        total_bytes: null,
        processed_files: 0,
        processed_bytes: 0,
        uploaded_bytes: 0,
        size_bytes: null,
        artifact_sha256: null,
      },
      f.scope,
    ).total_bytes,
  ).toBeNull();
  vi.mocked(request).mockResolvedValue({
    ...f.candidates,
    collection_version_id: "changed",
  });
  await expect(
    loadPackageCandidates(f.scope, f.data.identity.dataset_id, undefined, {
      collectionId: "cv_fixed",
      offset: 25,
    }),
  ).rejects.toThrow(/快照/);
});
it("reauthorizes matching download bytes and rejects a grant for another hash", async () => {
  const f = packageDeliveryFixture();
  vi.mocked(request).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: "1".repeat(64),
    size_bytes: f.job.size_bytes,
    url: "https://objects.test/file",
    expires_seconds: 300,
  });
  await expect(downloadPackageJob(f.scope, f.job)).rejects.toThrow(/身份/);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      scope: f.scope,
      cache: "no-store",
    }),
  );
});
it("does not bind annotation history from another data version", async () => {
  const f = packageDeliveryFixture(true),
    history = structuredClone(f.annotation.history);
  history.task.data_ref.version++;
  vi.mocked(request).mockResolvedValue(history);
  await expect(
    loadCollectionAnnotationHistory(f.scope, f.data, history.task.task_id),
  ).rejects.toThrow(/不一致/);
});
