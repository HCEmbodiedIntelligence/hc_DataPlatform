// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { loadEpisodeAnnotationTracks } from "../../features/datasets/episodes/episode-annotations";
import { episodeMigrationFixture } from "./testing/episode-detail-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());

function fixture() {
  const f = episodeMigrationFixture();
  f.data.frame_count = 300;
  f.view.frequency_hz = 30;
  const revision = {
    ...f.view.annotation.current_revision!,
    data_ref: structuredClone(f.data),
    tag_schema_id: "schema-tags",
    tag_schema_version: 3,
    tags: [
      {
        annotation_id: "pick",
        tag_id: "pick",
        path: ["pick"],
        label: "抓取",
        start_step: 30,
        end_step: 90,
        parent_annotation_id: null,
      },
      {
        annotation_id: "release",
        tag_id: "release",
        path: ["release"],
        label: "松手",
        start_step: 60,
        end_step: 120,
        parent_annotation_id: null,
      },
      {
        annotation_id: "move",
        tag_id: "move",
        path: ["move"],
        label: "移动",
        start_step: 60,
        end_step: 300,
        parent_annotation_id: "pick",
      },
    ],
  };
  vi.mocked(request).mockResolvedValue(revision);
  return { ...f, revision };
}

it("shows the saved, unsubmitted revision with hierarchy, overlap lanes and the Episode clock offset", async () => {
  const f = fixture();
  const result = await loadEpisodeAnnotationTracks(
    f.scope,
    f.view,
    "5000000000",
    "15000000000",
  );
  expect(result.count).toBe(3);
  expect(result.tracks).toHaveLength(3);
  expect(result.tracks[0]!.segments[0]).toMatchObject({
    label: "抓取",
    startNs: "6000000000",
    endNs: "8000000000",
    activatePlayback: true,
  });
  expect(result.tracks[1]!.segments[0]!.label).toBe("松手");
  expect(result.tracks[2]).toMatchObject({
    level: 1,
    segments: [{ label: "抓取 / 移动", endNs: "15000000000" }],
  });
  expect(
    result.tracks
      .flatMap((track) => track.segments)
      .every((tag) => !tag.editing),
  ).toBe(true);
  expect(request).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({
      method: "GET",
      path: `/annotation-tasks/${f.revision.task_id}/revisions/1`,
      scope: f.scope,
    }),
  );
});

it("resolves names from the saved schema version for legacy Tags", async () => {
  const f = fixture();
  f.revision.tags = [
    { ...f.revision.tags[0]!, label: "", path: ["phase", "pick"] },
  ];
  vi.mocked(request)
    .mockResolvedValueOnce(f.revision)
    .mockResolvedValueOnce({
      project_id: f.scope.projectId,
      schema_id: "schema-tags",
      version: 3,
      document: {
        nodes: [
          { tag_id: "phase", display_name: "操作" },
          { tag_id: "pick", display_name: "抓取" },
        ],
      },
    });
  const result = await loadEpisodeAnnotationTracks(
    f.scope,
    f.view,
    "0",
    "10000000000",
  );
  expect(result.tracks[0]!.segments[0]!.label).toBe("操作 / 抓取");
  expect(request).toHaveBeenLastCalledWith(
    expect.objectContaining({
      path: expect.stringContaining("/schema-tags/versions/3"),
    }),
  );
});

it.each(["version", "hash", "revision", "task", "range", "duplicate"])(
  "rejects %s mismatches instead of showing another annotation",
  async (field) => {
    const f = fixture();
    if (field === "version")
      f.revision.data_ref.data_version_id = "other-version";
    if (field === "hash") f.revision.content_hash = "e".repeat(64);
    if (field === "revision") f.revision.revision++;
    if (field === "task") f.revision.task_id = "another-task";
    if (field === "range") f.revision.tags[0]!.end_step = 301;
    if (field === "duplicate") f.revision.tags.push(f.revision.tags[0]!);
    await expect(
      loadEpisodeAnnotationTracks(f.scope, f.view, "0", "10000000000"),
    ).rejects.toThrow("不一致");
  },
);

it("keeps loading errors visible instead of returning an empty Tag list", async () => {
  const f = fixture();
  vi.mocked(request).mockRejectedValue(new Error("无法读取标注"));
  await expect(
    loadEpisodeAnnotationTracks(f.scope, f.view, "0", "10000000000"),
  ).rejects.toThrow("无法读取标注");
});
