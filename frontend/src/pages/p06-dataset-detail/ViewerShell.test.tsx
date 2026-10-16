// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeViewerShell } from "./ViewerShell";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { loadEpisodeAnnotationTracks } from "../../features/datasets/episodes/episode-annotations";
import { episodeMigrationFixture } from "./testing/episode-detail-fixture";
import type { EpisodeRevisionWire } from "../../features/datasets/api/wire-schemas";

vi.mock(
  "../../features/datasets/episodes/episode-detail",
  async (original) => ({
    ...(await original<
      typeof import("../../features/datasets/episodes/episode-detail")
    >()),
    loadEpisodeOverview: vi.fn(),
  }),
);
vi.mock("../../features/datasets/episodes/episode-annotations", () => ({
  loadEpisodeAnnotationTracks: vi.fn(),
}));

const state = vi.hoisted(() => ({
  data: {} as Record<string, unknown>,
  createIssue: vi.fn(),
  fetchLogicalSnapshot: vi.fn(),
  capabilities: new Set<string>(),
}));
vi.mock("../../features/datasets/api/logical-data", async (original) => ({
  ...(await original<
    typeof import("../../features/datasets/api/logical-data")
  >()),
  fetchLogicalSnapshot: state.fetchLogicalSnapshot,
}));
vi.mock("../../features/datasets/api", () => ({
  useViewerEpisodeQuery: () => ({
    data: state.data,
    isPending: false,
    isError: false,
  }),
}));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    loading: false,
    failed: false,
    has: (key: string) => state.capabilities.has(key),
  }),
}));
vi.mock("../../features/cleaning/routing", async (original) => ({
  ...(await original<typeof import("../../features/cleaning/routing")>()),
  createManualIssueCommand: state.createIssue,
}));
vi.mock("../../features/datasets/episodes/use-logical-robot-scene", () => ({
  useLogicalRobotScene: () => ({
    robotSceneUnavailableReason: "当前 Episode 未绑定机器人模型。",
  }),
}));
vi.mock("../../shared/scope/shell-store", async (original) => ({
  ...(await original<typeof import("../../shared/scope/shell-store")>()),
  useShellStore: (select: (state: object) => unknown) =>
    select({
      scope: {
        organizationId: "org-p06",
        projectId: "project-p06",
        regionCode: "region-p06",
      },
    }),
}));

const datasetId = "dataset_p06fixture";
const versionId = "version_p06fixture";
const episodeId = "episode_p06fixture";
const revision: EpisodeRevisionWire = {
  scope: {
    organization_id: "org-p06",
    project_id: "project-p06",
    region_code: "region-p06",
  },
  dataset_id: datasetId,
  version_id: versionId,
  episode_id: episodeId,
  revision_id: "revision_p06fixture",
  ordinal: 7,
  content_sha256: "a".repeat(64),
  started_at_ns: "0",
  duration_ns: "10000000000",
  streams: ["front", "wrist"].map((name) => ({
    episode_stream_id: `stream_${name}`,
    channel_path: `/camera/${name}/image`,
    kind: "RGB_VIDEO",
    t_start_ns: "0",
    t_end_ns: "10000000000",
    aligned_media_binding: null,
    data_binding: null,
  })),
};

function mount(search = "") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[
            `/datasets/${datasetId}/versions/${versionId}/episodes/${episodeId}/view${search}`,
          ]}
        >
          <Routes>
            <Route
              path="/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view"
              element={<EpisodeViewerShell />}
            />
            <Route path="/datasets/:datasetId" element={<p>原数据集页面</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

beforeEach(() => {
  state.capabilities = new Set([
    "episode.read",
    "manual_issue.create",
    "manual_issue.read",
  ]);
  state.data = {
    revision,
    episode: { ordinal: 7, robotId: null },
    bootstrap: {
      snapshotToken: "fixed-browser-snapshot-token",
      allowedActions: [
        { action: "CREATE_ISSUE", allowed: true, blockedReasons: [] },
      ],
    },
  };
  state.fetchLogicalSnapshot.mockResolvedValue({
    snapshot_id: `snapshot_${"b".repeat(64)}`,
    snapshot: {
      streams: revision.streams.map((s) => ({
        stream_id: s.episode_stream_id,
        name: s.channel_path,
        kind: s.kind,
        sample_count: 0,
        temporal: true,
        start_ns: s.t_start_ns,
        end_ns: s.t_end_ns,
      })),
    },
  });
  state.createIssue.mockResolvedValue({ id: "issue_created" });
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computed(element),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("dataset Episode shared workbench", () => {
  it("uses the annotation layout in read-only mode with camera selection and a single shared timeline", async () => {
    const { container } = mount();
    const workbench = container.querySelector(
      '[data-mode="published-readonly"]',
    );
    expect(workbench).toHaveAttribute("data-layout", "workspace");
    expect(workbench).toHaveAttribute("data-read-only", "true");
    expect(container.querySelector("[data-episode-workbench]")).toHaveAttribute(
      "data-workspace-layout",
      "synchronized",
    );
    expect(container.querySelectorAll(".viewer-timeline")).toHaveLength(1);
    expect(
      container.querySelectorAll(".viewer-playback-controls"),
    ).toHaveLength(1);
    expect(screen.getByLabelText("机器人姿态同步视图")).toBeInTheDocument();
    expect(
      await screen.findByText("四路视频 · 已接入 2 / 4 路"),
    ).toBeInTheDocument();
    expect(state.fetchLogicalSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({
        datasetId,
        versionId,
        episodeId,
        revisionId: revision.revision_id,
        snapshotToken: "fixed-browser-snapshot-token",
      }),
      expect.any(AbortSignal),
    );
    expect(
      screen.queryByRole("button", { name: "保存标注" }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("显示视频"), {
      target: { value: "stream_wrist" },
    });
    expect(workbench).toHaveAttribute("data-camera-count", "1");
    expect(screen.getByText("视频视图 · 当前 1 / 2 路")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "数据信息" }));
    expect(await screen.findByText(revision.revision_id)).toBeInTheDocument();
    expect(state.createIssue).not.toHaveBeenCalled();
  });

  it("shows saved Tags for the exact Episode data and has no issue or range editing controls", async () => {
    const f = episodeMigrationFixture();
    Object.assign(f.data.identity, {
      organization_id: "org-p06",
      project_id: "project-p06",
      region_code: "region-p06",
      dataset_id: datasetId,
      episode_id: episodeId,
    });
    const boundRevision = structuredClone(revision);
    boundRevision.streams[0]!.package_binding = {
      data: f.data,
      rollout_id: f.data.identity.rollout_id,
      feature_key: "front",
      modality_key: "/camera/front/image",
      value_kind: null,
      start_step: 0,
      end_step: f.data.frame_count,
    };
    state.data.revision = boundRevision;
    state.capabilities.add("dataset_version.read");
    state.capabilities.add("annotation_task.read");
    vi.mocked(loadEpisodeOverview).mockResolvedValue(f.view);
    vi.mocked(loadEpisodeAnnotationTracks).mockResolvedValue({
      count: 1,
      tracks: [
        {
          id: "tags",
          label: "Tag L1",
          level: 0,
          segments: [
            {
              id: "tag-pick",
              label: "抓取物体",
              startNs: "1000000000",
              endNs: "2000000000",
              activatePlayback: true,
            },
          ],
        },
      ],
    });
    mount(
      "?selectionStartNs=1000000000&selectionEndNs=2000000000&streamId=stream_front",
    );
    const tag = await screen.findByRole("button", { name: /从“抓取物体”/ });
    expect(tag).not.toHaveAttribute("data-editable");
    expect(loadEpisodeOverview).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: "project-p06" }),
      f.data,
      { annotationTaskId: undefined },
      expect.any(AbortSignal),
    );
    expect(loadEpisodeAnnotationTracks).toHaveBeenCalledWith(
      expect.anything(),
      f.view,
      "0",
      "10000000000",
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/1 个 Tag/)).toBeVisible();
    expect(screen.queryByLabelText("数据通道")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看问题清单" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "添加人工问题" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "设为入点" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("当前标注")).not.toBeInTheDocument();
    expect(state.createIssue).not.toHaveBeenCalled();
  });

  it("keeps issue creation gated and returns to the dataset without opening annotation", () => {
    state.capabilities.delete("manual_issue.create");
    mount(
      `?returnTo=${encodeURIComponent(`/datasets/${datasetId}?tab=episodes&versionId=${versionId}`)}`,
    );
    expect(
      screen.queryByRole("button", { name: "添加人工问题" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回数据集" }));
    expect(screen.getByText("原数据集页面")).toBeInTheDocument();
    expect(state.createIssue).not.toHaveBeenCalled();
  });
});
