import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  checkedPackageJob,
  createPackageBatch,
  downloadPackageJob,
  loadPackageCandidates,
  loadPackageJob,
  listPackageJobs,
  loadCollectionAnnotationHistory,
} from "../../features/exports/package-deliveries";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it("loads collection exports containing historical and current annotation plans", async () => {
  const f = packageDeliveryFixture(true);
  const legacy = structuredClone(f.job);
  f.job.plan.processor_version = "hc-lerobot-collection/5";
  legacy.job_id = "ed_" + "1".repeat(64);
  legacy.plan.members[0]!.annotation!.processor_version = "hc-annotated-delivery-queue/1";
  vi.mocked(request).mockResolvedValue([f.job, legacy]);
  await expect(listPackageJobs(f.scope)).resolves.toEqual([f.job, legacy]);
});
it("creates a queued batch of three current annotated episodes before totals are known", async () => {
  const f = packageDeliveryFixture(true);
  const member = f.job.plan.members[0]!;
  const job = {
    ...f.job,
    status: "PENDING",
    phase: "queued",
    artifact_sha256: null,
    size_bytes: null,
    total_files: null,
    total_bytes: null,
    processed_files: 0,
    processed_bytes: 0,
    uploaded_bytes: 0,
    plan: {
      ...f.job.plan,
      members: [1, 2, 3].map((index) => {
        const next = structuredClone(member);
        next.data.data_version_id = "dv_" + String(index).repeat(64);
        next.data.identity.episode_id = `episode-${index}`;
        next.annotation!.binding.data = structuredClone(next.data);
        next.annotation!.binding.task_id = `task-${index}`;
        return next;
      }),
    },
  };
  const members = job.plan.members.map(({ data, annotation }) => ({
    data,
    annotation: {
      task_id: annotation!.binding.task_id,
      revision: annotation!.binding.revision,
      track: annotation!.binding.track,
      allow_unreviewed: true,
    },
  }));
  vi.mocked(request).mockResolvedValue(job);
  await expect(createPackageBatch(f.scope, members, "rgbd", "three-episodes")).resolves.toEqual(job);
});
it("polls the exact export and rejects another job or a changed depth selection", async () => {
  const f = packageDeliveryFixture();
  vi.mocked(request).mockResolvedValue(f.job);
  expect(await loadPackageJob(f.scope, f.job)).toEqual(f.job);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "GET",
      path: expect.stringContaining(f.job.job_id),
      cache: "no-store",
    }),
  );
  vi.mocked(request).mockResolvedValue({
    ...f.job,
    job_id: "ed_" + "d".repeat(64),
  });
  await expect(loadPackageJob(f.scope, f.job)).rejects.toThrow(/任务已变化/);
  const changed = structuredClone(f.job);
  if (changed.plan.format !== "hc-collection-delivery-plan/1")
    throw new Error("fixture");
  changed.plan.modalities = "rgb";
  changed.plan.members[0]!.excluded_depth_features = Object.keys(
    changed.plan.members[0]!.selected_depth_profiles,
  );
  changed.plan.members[0]!.selected_depth_profiles = {};
  vi.mocked(request).mockResolvedValue(changed);
  await expect(loadPackageJob(f.scope, f.job)).rejects.toThrow(/任务已变化/);
});
it.each([{ segments: [] }, { segments: ["first"] }])(
  "pins the saved revision with segment selection $segments",
  async ({ segments }) => {
    const f = packageDeliveryFixture(true),
      a = f.annotation.job.plan.binding;
    a.track.annotation_ids = segments;
    f.job.plan.members[0]!.annotation!.binding.track.annotation_ids = segments;
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
  },
);
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

it.each(["annotated", "dataset"] as const)("keeps the %s filter on subsequent candidate pages", async (stage) => {
  const f = packageDeliveryFixture();
  vi.mocked(request).mockResolvedValue(f.candidates);
  await loadPackageCandidates(f.scope, f.data.identity.dataset_id, undefined, { collectionId: "cv_fixed", offset: 25 }, stage);
  expect(request).toHaveBeenCalledWith(expect.objectContaining({
    query: expect.objectContaining({ data_stage: stage, collection_version_id: "cv_fixed", offset: 25 }),
  }));
});

it.each(["mcap", "original", "holobrain", "hdf5"])(
  "requests %s using the common delivery endpoint",
  async (mode) => {
    const f = packageDeliveryFixture(true);
    if (f.job.plan.format !== "hc-collection-delivery-plan/1")
      throw new Error("fixture");
    f.job.plan.archive_format = mode === "mcap" ? "mcap_collection_zip/3" : mode === "holobrain" ? "holobrain_collection_zip/2" : `${mode}_collection_zip/1`;
    f.job.plan.processor_version = "hc-export/1";
    f.job.plan.reader_version = mode === "mcap" ? "hc-selected-mcap/2" : "original-bytes/1";
    vi.mocked(request).mockResolvedValueOnce([
      {
        id: mode,
        title: mode,
        archive_format: f.job.plan.archive_format,
        supports_annotations: true,
        requires_originals: mode === "original",
      },
    ]);
    vi.mocked(request).mockResolvedValueOnce(f.job);
    const binding = f.annotation.job.plan.binding;
    const members = [
      {
        data: f.data,
        annotation: {
          task_id: binding.task_id,
          revision: binding.revision,
          allow_unreviewed: true,
          track: binding.track,
        },
      },
    ];
    expect(
      await createPackageBatch(f.scope, members, mode, "universal"),
    ).toEqual(f.job);
    expect(request).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: "POST",
        path: expect.stringMatching(/\/collections$/u),
        body: { members, modalities: "rgbd", output_format: mode },
      }),
    );
  },
);
