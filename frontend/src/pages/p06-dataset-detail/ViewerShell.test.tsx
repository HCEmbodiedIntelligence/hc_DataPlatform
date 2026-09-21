// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeViewerShell } from "./ViewerShell";
import type { EpisodeRevisionWire } from "../../features/datasets/api/wire-schemas";

const state = vi.hoisted(() => ({
  data: {} as Record<string, unknown>,
  createIssue: vi.fn(),
  capabilities: new Set<string>(),
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
vi.mock("../../features/viewer/use-episode-robot-scene", () => ({
  useEpisodeRobotScene: () => ({
    robotSceneUnavailableReason: "当前 Episode 未绑定机器人模型。",
  }),
}));
vi.mock("../../shared/scope/shell-store", () => ({
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
      allowedActions: [
        { action: "CREATE_ISSUE", allowed: true, blockedReasons: [] },
      ],
    },
  };
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
    expect(screen.getByText("四路视频 · 已接入 2 / 4 路")).toBeInTheDocument();
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

  it("retains the immutable revision and selected time range when recording an issue", async () => {
    mount(
      "?selectionStartNs=1000000000&selectionEndNs=2000000000&streamId=stream_front",
    );
    fireEvent.click(screen.getByRole("button", { name: "添加人工问题" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "说明" }), {
      target: { value: "视频缺帧" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认添加问题" }));
    await waitFor(() => expect(state.createIssue).toHaveBeenCalledOnce());
    expect(state.createIssue).toHaveBeenCalledWith(
      expect.objectContaining({
        datasetId,
        versionId,
        episodeId,
        revisionId: revision.revision_id,
        streamId: "stream_front",
        startNs: "1000000000",
        endNs: "2000000000",
        note: "视频缺帧",
      }),
    );
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
