import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  commandTagEpisode,
  createTagEpisode,
  listTagEpisodes,
  type TagEpisodeJob,
} from "./tag-episodes";
import { tagEpisodeFixture } from "./testing/tag-episode-fixture";
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());

it.each<[string, (j: TagEpisodeJob) => void]>([
  [
    "tenant",
    (j) => {
      j.selection.parent.identity.organization_id = "other";
    },
  ],
  [
    "revision",
    (j) => {
      j.selection.revision_content_hash = "9".repeat(64);
    },
  ],
  [
    "tag interval",
    (j) => {
      j.selection.end_step = 3;
    },
  ],
  [
    "child scope",
    (j) => {
      j.committed_data!.identity.region_code = "other";
    },
  ],
  [
    "child frame count",
    (j) => {
      j.committed_data!.frame_count = 3;
    },
  ],
  [
    "viewer collection",
    (j) => {
      j.collection_version_id = "different-collection";
    },
  ],
  [
    "inherited task",
    (j) => {
      j.annotation_task_id = j.selection.task_id;
    },
  ],
  [
    "unfinished READY",
    (j) => {
      j.annotation_task_id = null;
    },
  ],
])("rejects a mismatched %s", async (_, change) => {
  const f = tagEpisodeFixture();
  const job = structuredClone(f.job);
  change(job);
  vi.mocked(request).mockResolvedValue([job]);
  await expect(listTagEpisodes(f.scope, f.history)).rejects.toThrow(/不一致/);
});
it("keeps exact epoch nanoseconds and scopes uncached pagination", async () => {
  const f = tagEpisodeFixture();
  vi.mocked(request).mockResolvedValue([f.job]);
  const jobs = await listTagEpisodes(
    f.scope,
    f.history,
    undefined,
    "td_" + "8".repeat(64),
  );
  expect(jobs[0]!.selection.start_timestamp_ns).toBe("1790583427123456789");
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      scope: f.scope,
      path: `/projects/${f.scope.projectId}/regions/${f.scope.regionCode}/annotation-tasks/${f.task.task_id}/tag-episodes`,
      cache: "no-store",
      query: { limit: 25, before: "td_" + "8".repeat(64) },
    }),
  );
});
it("rejects a valid response for a different selected Tag", async () => {
  const f = tagEpisodeFixture();
  vi.mocked(request).mockResolvedValue(f.job);
  await expect(
    createTagEpisode(
      f.scope,
      f.history,
      {
        revision: f.revision.revision,
        annotation_id: "cross",
        allow_unreviewed: true,
      },
      "fixed-key",
    ),
  ).rejects.toThrow(/本次选择不一致/);
});
it("retry pins the job and its generation", async () => {
  const f = tagEpisodeFixture();
  vi.mocked(request).mockResolvedValue(f.job);
  await commandTagEpisode(f.scope, f.history, f.job, "retry");
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      path: expect.stringContaining(`${f.job.job_id}/retry`),
      body: { expected_generation: 1 },
    }),
  );
  vi.mocked(request).mockResolvedValue({
    ...f.job,
    job_id: "td_" + "9".repeat(64),
  });
  await expect(
    commandTagEpisode(f.scope, f.history, f.job, "retry"),
  ).rejects.toThrow(/选定任务不一致/);
});
