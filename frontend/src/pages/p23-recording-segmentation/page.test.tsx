// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProviderHarness } from "../../app/providers";
import { useShellStore } from "../../shared/scope/shell-store";
import type {
  ContinuousRecording,
  RecordingGateway,
  RecordingScope,
  RecordingVideoSourceEnvelope,
  SaveSliceDraftCommand,
  SliceRevisionEnvelope,
} from "./api";
import RecordingSegmentationPageEntry from "./page";

expect.extend(matchers);

const scope = {
  organizationId: "org-test",
  projectId: "project-test",
  regionCode: "cn-test",
} as const;

const recording: ContinuousRecording = {
  schema_version: "continuous-recording/v2",
  ingest_mode: "SLICE_FIRST",
  default_task_labels: [],
  scope: {
    organization_id: scope.organizationId,
    project_id: scope.projectId,
    region_code: scope.regionCode,
  },
  recording_id: "recording_three_hours",
  file_name: "T1_20260831_090000_000.mcap",
  source_file_name: "raw.mcap",
  robot_name: "G1 测试机",
  task_code: "00000001",
  recording_upload_id: "11111111-1111-4111-8111-111111111111",
  upload_session_id: null,
  rollout_id: "rollout-1",
  data_package_id: "package-1",
  collection_task_id: "task-1",
  collection_job_id: "job-1",
  robot_id: "robot-g1",
  device_id: "device-1",
  capture_started_at: "2026-08-31T01:00:00Z",
  capture_ended_at: "2026-08-31T04:00:00Z",
  duration_ns: "10800000000000",
  source_sha256: "a".repeat(64),
  manifest_fingerprint: "b".repeat(64),
  video_asset_count: 1,
  status: "READY_FOR_SLICING",
  current_revision: 0,
  finalized_revision: null,
  etag: '"v1"',
};

function makeGateway(): RecordingGateway {
  return {
    list: vi.fn(async () => ({ items: [recording], total: 1 })),
    detail: vi.fn(async () => ({
      data: recording,
      current_slice_revision: null,
    })),
    videoSources: vi.fn(
      async (): Promise<RecordingVideoSourceEnvelope> => ({
        sources: [
          {
            schema_version: "recording-video-source/v1",
            recording_id: recording.recording_id,
            asset_id: "22222222-2222-4222-8222-222222222222",
            camera_id: "front",
            media_type: "video/mp4",
            source_url:
              "https://objects.example.test/front.mp4?signature=redacted",
            duration_ns: recording.duration_ns,
            fps: 30,
            codec: "h264",
            modality: "rgb",
            preview_status: "READY",
            expires_at: "2026-08-31T05:00:00Z",
            byte_range_supported: true,
            materialization: "ORIGINAL_RECORDING",
          },
        ],
      }),
    ),
    sensorWindow: vi.fn(async (_scope, recordingId, query) => ({
      schema_version: "recording-sensor-window/v1" as const,
      recording_id: recordingId,
      topic: "/robot/joint_states",
      start_offset_ns: query.startOffsetNs,
      end_offset_ns: query.endOffsetNs,
      samples: [
        {
          offset_ns: query.startOffsetNs,
          source_timestamp_ns: query.startOffsetNs,
          value: {
            name: ["shoulder", "elbow"],
            position: [0, 0],
          },
        },
      ],
      truncated: false,
    })),
    processing: vi.fn(async () => ({ items: [], total: 0, qc_reports: [] })),
    saveDraft: vi.fn(
      async (
        _scope: RecordingScope,
        _recordingId: string,
        command: SaveSliceDraftCommand,
        _etag: string,
      ): Promise<SliceRevisionEnvelope> => ({
        recording: { ...recording, current_revision: 1, etag: '"v2"' },
        revision: {
          schema_version: "recording-slice-revision/v1",
          scope: recording.scope,
          recording_id: recording.recording_id,
          revision: 1,
          status: "DRAFT",
          authoring_mode: "HUMAN",
          created_by: "operator-test",
          slices: command.slices.map((slice, ordinal) => ({
            schema_version: "recording-episode-slice/v1",
            scope: recording.scope,
            recording_id: recording.recording_id,
            revision: 1,
            ordinal,
            episode_id: slice.episode_id,
            start_offset_ns: slice.start_offset_ns,
            end_offset_ns: slice.end_offset_ns,
            started_at: "2026-08-31T01:00:00Z",
            ended_at: "2026-08-31T01:00:01Z",
            source_sha256: recording.source_sha256,
            source_upload_session_id: null,
            source_recording_upload_id: recording.recording_upload_id,
            title: slice.title,
            task_label: slice.task_label,
            task_labels: slice.task_labels,
            notes: slice.notes,
          })),
        },
      }),
    ),
    finalize: vi.fn(async () => {
      throw new Error("not used in this test");
    }),
  };
}

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function makeSwitchingGateway() {
  const gateway = makeGateway();
  const second = {
    ...recording,
    recording_id: "recording_second",
    file_name: "T1_20260830_090000_000.mcap",
    rollout_id: "rollout-second",
    capture_started_at: "2026-08-30T01:00:00Z",
    duration_ns: "5000000000",
  };
  const otherTask = {
    ...second,
    recording_id: "recording_other_task",
    file_name: "T2_20260829_090000_000.mcap",
    task_code: "00000002",
    collection_task_id: "task-2",
    capture_started_at: "2026-08-29T01:00:00Z",
  };
  vi.mocked(gateway.list).mockResolvedValue({
    items: [recording, second, otherTask],
    total: 3,
  });
  vi.mocked(gateway.detail).mockImplementation(async (_scope, id) => ({
    data:
      id === recording.recording_id
        ? recording
        : id === second.recording_id
          ? second
          : otherTask,
    current_slice_revision: null,
  }));
  return { gateway, second };
}

async function markFirstSlice(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /开始标记/ }));
  fireEvent.keyDown(screen.getByRole("slider", { name: "共享播放位置" }), {
    key: "ArrowRight",
  });
  await user.click(screen.getByRole("button", { name: /设为出点并添加/ }));
}

function renderPage(gateway: RecordingGateway, initialEntry: string, reactStrictMode = false) {
  return render(
    <ProviderHarness>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/recordings"
            element={
              <RecordingSegmentationPageEntry
                gateway={gateway}
                capabilityOverride="manage"
              />
            }
          />
          <Route
            path="/recordings/:recordingId/slice"
            element={
              <RecordingSegmentationPageEntry
                gateway={gateway}
                capabilityOverride="manage"
              />
            }
          />
        <Route path="/annotations/tasks/:taskId" element={<h1>标注时间线</h1>} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </ProviderHarness>,
    { reactStrictMode },
  );
}

beforeEach(() => {
  useShellStore.getState().setScope(scope);
  useShellStore.setState({
    authorization: null,
    authorizationFailed: false,
    authorizationLoading: false,
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  });
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperties(HTMLMediaElement.prototype, {
    load: { configurable: true, value: vi.fn() },
    pause: { configurable: true, value: vi.fn() },
    play: { configurable: true, value: vi.fn(async () => undefined) },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("P23 recording segmentation", () => {
  it("resumes confirmed intervals on an already waiting reprocessed recording", async () => {
    const user = userEvent.setup();
    const gateway = makeGateway();
    const sample = await gateway.saveDraft(scope, "previous-recording", { slices: [{
      episode_id: "episode_0001", start_offset_ns: "10520832000", end_offset_ns: "76800000000",
      title: "已确认区间", task_labels: ["进行测试"],
    }] }, '"v1"');
    vi.mocked(gateway.saveDraft).mockClear();
    const previous = { ...sample.revision, recording_id: "previous-recording", status: "FINALIZED" as const };
    vi.mocked(gateway.detail).mockResolvedValue({
      data: recording, current_slice_revision: null, reusable_slices: previous,
    });
    vi.mocked(gateway.finalize).mockResolvedValue({
      recording: { ...recording, status: "SLICED", finalized_revision: 1 },
      revision: { ...previous, recording_id: recording.recording_id, reused_from: {
        recording_id: previous.recording_id, revision: previous.revision, episode_count: 1,
      } },
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await user.click(await screen.findByRole("button", { name: "沿用上次切片并继续入库" }));
    await waitFor(() => expect(gateway.finalize).toHaveBeenCalledWith(scope, recording.recording_id, 0, recording.etag));
    expect(gateway.saveDraft).not.toHaveBeenCalled();
    expect(await screen.findByText("已沿用上次确认的切片")).toBeVisible();
  });

  it.each(["PENDING_QC", "QC_RUNNING", "QC_FAILED", "QC_ERROR"] as const)(
    "blocks slice authoring while recording quality is %s", async (status) => {
      const gateway = makeGateway();
      vi.mocked(gateway.detail).mockResolvedValue({
        data: { ...recording, status }, current_slice_revision: null,
      });
      renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
      expect(await screen.findByRole("button", { name: "提交成片" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
      expect(screen.getByText("上传完成后自动检查整段录制，通过后才能切片。")).toBeVisible();
      expect(gateway.saveDraft).not.toHaveBeenCalled();
      expect(gateway.finalize).not.toHaveBeenCalled();
    },
  );

  it("retries failed whole-recording quality without making slices", async () => {
    const gateway = makeGateway();
    vi.mocked(gateway.detail).mockResolvedValue({
      data: { ...recording, status: "QC_ERROR", quality_error_code: "CAPTURE_PLAN_REQUIRED" },
      current_slice_revision: null,
    });
    gateway.retryQuality = vi.fn(async () => ({
      data: { ...recording, status: "PENDING_QC" as const }, current_slice_revision: null,
    }));
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    await userEvent.click(await screen.findByRole("button", { name: "重新质检" }));
    await waitFor(() => expect(gateway.retryQuality).toHaveBeenCalledWith(
      scope, recording.recording_id, expect.any(String),
    ));
    expect((await screen.findAllByText("等待整段质检"))[0]).toBeVisible();
    expect(gateway.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps unchecked recordings out of the slicing queue", async () => {
    const gateway = makeGateway();
    vi.mocked(gateway.list).mockResolvedValue({items: [
      recording, { ...recording, recording_id: "unchecked", file_name: "unchecked.mcap", status: "PENDING_QC" },
    ], total: 2});
    renderPage(gateway, "/recordings");
    expect(await screen.findByRole("button", { name: "开始切片" })).toBeVisible();
    expect(screen.queryByText("unchecked.mcap")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: /录制质检/ }));
    expect(await screen.findByText("unchecked.mcap")).toBeVisible();
    expect(screen.queryByRole("button", { name: "开始切片" })).not.toBeInTheDocument();
  });

  it("starts video authorization while recording details are still loading", async () => {
    const gateway = makeGateway();
    vi.mocked(gateway.detail).mockImplementation(() => new Promise(() => {}));
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    await waitFor(() => expect(gateway.videoSources).toHaveBeenCalledWith(
      scope, recording.recording_id, expect.any(AbortSignal),
    ));
  });
  it("switches original recordings in place, resets the player and keeps the chosen task filter", async () => {
    const { gateway, second } = makeSwitchingGateway();
    const user = userEvent.setup();
    renderPage(
      gateway,
      `/recordings/${recording.recording_id}/slice?episode=episode_old`,
    );
    const sidebar = await screen.findByRole("complementary", {
      name: "原始录制切换",
    });
    const workspace = screen.getByLabelText("录制切片工作台");
    await user.click(
      within(sidebar).getByRole("button", { name: "收起原始录制" }),
    );
    expect(workspace).toHaveAttribute("data-navigator-collapsed", "true");
    expect(within(sidebar).queryByRole("searchbox")).not.toBeInTheDocument();
    expect(
      within(sidebar).getByRole("button", { name: "展开原始录制" }),
    ).toHaveAttribute("aria-expanded", "false");
    await user.click(
      within(sidebar).getByRole("button", { name: "展开原始录制" }),
    );
    expect(workspace).toHaveAttribute("data-navigator-collapsed", "false");
    expect(
      await within(sidebar).findByRole("button", {
        name: `切换到录制 ${second.file_name}`,
      }),
    ).toBeVisible();
    expect(
      within(sidebar).queryByRole("button", {
        name: "切换到录制 T2_20260829_090000_000.mcap",
      }),
    ).not.toBeInTheDocument();
    await user.click(
      within(sidebar).getByRole("checkbox", { name: "仅当前采集任务" }),
    );
    await user.click(within(sidebar).getByRole("button", { name: "下一条" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/recordings/${second.recording_id}/slice`,
      ),
    );
    expect(
      await screen.findByRole("checkbox", { name: "仅当前采集任务" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: `切换到录制 ${second.file_name}` }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("slider", { name: "共享播放位置" }),
    ).toHaveAttribute("aria-valuenow", "0");
    expect(gateway.saveDraft).not.toHaveBeenCalled();
    expect(gateway.processing).not.toHaveBeenCalled();
    expect(screen.getByLabelText("录制切片工作台")).toBe(workspace);
    expect(screen.getByRole("complementary", { name: "原始录制切换" })).toBe(sidebar);
    await user.click(screen.getByRole("button", { name: "上一条" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/recordings/${recording.recording_id}/slice`,
      ),
    );
  });

  it("keeps the workspace visible during a slow switch and resets a same-duration recording", async () => {
    const { gateway, second } = makeSwitchingGateway();
    const next = { ...second, duration_ns: recording.duration_ns };
    let resolveNext!: (value: Awaited<ReturnType<RecordingGateway["detail"]>>) => void;
    vi.mocked(gateway.detail).mockImplementation(async (_scope, id) =>
      id === second.recording_id
        ? new Promise((resolve) => { resolveNext = resolve; })
        : { data: recording, current_slice_revision: null },
    );
    const user = userEvent.setup();
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    const workspace = await screen.findByLabelText("录制切片工作台");
    const sidebar = screen.getByRole("complementary", { name: "原始录制切换" });
    const slider = screen.getByRole("slider", { name: "共享播放位置" });
    await user.selectOptions(screen.getByRole("combobox", { name: "显示视频" }), "__all__");
    fireEvent.keyDown(slider, { key: "End" });
    await waitFor(() => expect(slider).not.toHaveAttribute("aria-valuenow", "0"));
    await user.click(screen.getByRole("button", { name: /开始标记/ }));
    await user.click(await within(sidebar).findByRole("button", { name: "下一条" }));
    await screen.findByText("正在切换录制…");
    expect(workspace).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("录制切片工作台")).toBe(workspace);
    expect(screen.getByRole("complementary", { name: "原始录制切换" })).toBe(sidebar);
    expect(slider.isConnected).toBe(true);
    expect(workspace.querySelector("[data-episode-workbench]")).toHaveAttribute("inert");
    expect(screen.getByTestId("location")).toHaveTextContent(recording.recording_id);
    await waitFor(() => expect(gateway.videoSources).toHaveBeenCalledWith(
      scope, second.recording_id, expect.any(AbortSignal),
    ));

    await act(async () => resolveNext({ data: next, current_slice_revision: null }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(second.recording_id));
    expect(screen.getByLabelText("录制切片工作台")).toBe(workspace);
    expect(screen.getByRole("complementary", { name: "原始录制切换" })).toBe(sidebar);
    expect(screen.getByRole("slider", { name: "共享播放位置" })).toBe(slider);
    expect(slider).toHaveAttribute("aria-valuenow", "0");
    expect(workspace).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("combobox", { name: "显示视频" })).toHaveValue("__all__");
    expect(screen.getByRole("button", { name: /开始标记/ })).toBeVisible();
    expect(gateway.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps the current recording and unsaved slices when the next recording cannot load", async () => {
    const { gateway, second } = makeSwitchingGateway();
    vi.mocked(gateway.detail).mockImplementation(async (_scope, id) => {
      if (id === second.recording_id) throw new Error("next recording unavailable");
      return { data: recording, current_slice_revision: null };
    });
    const user = userEvent.setup();
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    await markFirstSlice(user);
    const workspace = screen.getByLabelText("录制切片工作台");
    await user.click(screen.getByRole("button", { name: "下一条" }));
    await user.click(screen.getByRole("button", { name: "放弃修改并切换" }));
    await screen.findByText("切换录制失败");
    expect(screen.getByTestId("location")).toHaveTextContent(recording.recording_id);
    expect(screen.getByLabelText("录制切片工作台")).toBe(workspace);
    expect(workspace).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText("有未保存修改")).toBeVisible();
    expect(screen.getByRole("button", { name: "下一条" })).toBeEnabled();
    expect(screen.getAllByText("Episode 0001")[0]).toBeVisible();
    expect(gateway.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps unsaved slices when switching is cancelled, then saves the current source before switching", async () => {
    const { gateway, second } = makeSwitchingGateway();
    const user = userEvent.setup();
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    await markFirstSlice(user);
    await user.click(screen.getByRole("button", { name: "下一条待分片" }));
    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      recording.recording_id,
    );
    expect(gateway.saveDraft).not.toHaveBeenCalled();
    expect(screen.getByText("有未保存修改")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: `切换到录制 ${second.file_name}` }),
    );
    await user.click(screen.getByRole("button", { name: "保存草稿并切换" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        second.recording_id,
      ),
    );
    expect(gateway.saveDraft).toHaveBeenCalledWith(
      scope,
      recording.recording_id,
      {
        slices: [expect.objectContaining({ episode_id: "episode_0001" })],
      },
      recording.etag,
    );
    expect(
      await screen.findByRole("button", { name: "保存草稿" }),
    ).toBeVisible();
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
    expect(screen.queryByText("Episode 0001")).not.toBeInTheDocument();
  });

  it("stays on the original recording after a save failure and supports explicit discard", async () => {
    const { gateway, second } = makeSwitchingGateway();
    vi.mocked(gateway.saveDraft).mockRejectedValue(new Error("save failed"));
    const user = userEvent.setup();
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    await markFirstSlice(user);
    await user.click(screen.getByRole("button", { name: "下一条" }));
    await user.click(screen.getByRole("button", { name: "保存草稿并切换" }));
    expect(
      await screen.findByText("保存失败，仍停留在当前录制，修改已保留"),
    ).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent(
      recording.recording_id,
    );
    expect(screen.getByText("有未保存修改")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "放弃修改并切换" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        second.recording_id,
      ),
    );
    expect(gateway.saveDraft).toHaveBeenCalledTimes(1);
  });

  it.each(["lerobot_v3", "legacy_lance"])(
    "previews generated %s episodes without loading QC or admission details",
    async (format) => {
      const gateway = makeGateway();
      const packages = format === "lerobot_v3";
      const draft = await gateway.saveDraft(
        scope,
        recording.recording_id,
        {
          slices: [
            {
              episode_id: "episode_cut",
              start_offset_ns: "0",
              end_offset_ns: "133333333",
            },
          ],
        },
        recording.etag,
      );
      vi.mocked(gateway.detail).mockResolvedValue({
        data: { ...draft.recording, status: "SLICED", finalized_revision: 1 },
        current_slice_revision: { ...draft.revision, status: "FINALIZED" },
      });
      vi.mocked(gateway.processing).mockResolvedValue({
        items: [
          {
            schema_version: "recording-episode/v1",
            scope: recording.scope,
            recording_id: recording.recording_id,
            episode_id: "episode_cut",
            finalized_revision: 1,
            start_offset_ns: "0",
            end_offset_ns: "133333333",
            status: "READY",
            dataset_id: "dataset_ready",
            dataset_episode_id: "episode_package",
            dataset_version: 7,
            lance_version: packages ? null : 12,
            annotation_task_id: "annotation_test",
            collection_version_id: packages ? "cv_test" : null,
            data_ref: packages
              ? {
                  identity: {
                    ...recording.scope,
                    dataset_id: "dataset_ready",
                    episode_id: "episode_package",
                    rollout_id: `${recording.recording_id}:episode_cut`,
                  },
                  data_version_id: "dv_test",
                  version: 1,
                  content_hash: "a".repeat(64),
                  storage_format: "lerobot_v3",
                  schema_snapshot_id: "schema_test",
                  package_prefix: "episodes/test",
                  frame_count: 4,
                }
              : null,
          },
        ],
        total: 1,
        qc_reports: [],
      });
      renderPage(gateway, "/recordings/recording_three_hours/slice");
      expect(
        await screen.findByRole("button", { name: "预览 Episode 1" }),
      ).toBeVisible();
      expect(gateway.processing).not.toHaveBeenCalled();
      expect(screen.queryByText("质检明细")).not.toBeInTheDocument();
      expect(screen.queryByText("处理阶段")).not.toBeInTheDocument();
    },
  );

  it("uses the shared workflow queue for pending, review, and sliced recordings", async () => {
    const gateway = makeGateway();
    vi.mocked(gateway.list).mockResolvedValue({
      items: [
        recording,
        {
          ...recording,
          recording_id: "recording_with_draft",
          current_revision: 2,
          etag: '"v2"',
        },
        {
          ...recording,
          recording_id: "recording_sliced",
          status: "SLICED",
          current_revision: 3,
          finalized_revision: 3,
          etag: '"v3"',
        },
      ],
      total: 3,
    });
    renderPage(gateway, "/recordings");

    const stages = await screen.findByRole("navigation", {
      name: "录制切片状态",
    });
    expect(
      within(stages).getByRole("link", { name: /^切片与标注，.*1 项/u }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(stages).getByRole("link", { name: /^待检查，.*1 项/u }),
    ).toHaveAttribute("href", "/recordings?stage=REVIEW");
    expect(
      within(stages).getByRole("link", { name: /^历史切片，.*1 项/u }),
    ).toHaveAttribute("href", "/recordings?stage=SLICED");
  });

  it("opens the manual cutter from the continuous-recording list", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings");

    expect(
      await screen.findByRole("heading", { name: "录制切片" }),
    ).toBeVisible();
    expect(await screen.findByText(recording.file_name!)).toBeVisible();
    expect(screen.getByText("G1 测试机")).toBeVisible();
    expect(screen.getByText("1", { selector: "small" })).toBeVisible();
    expect(screen.queryByText(recording.recording_id)).not.toBeInTheDocument();
    expect(
      screen.queryByText(recording.collection_task_id),
    ).not.toBeInTheDocument();
    await user.click(screen.getByText(recording.file_name!));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/recordings/recording_three_hours/slice",
    );
  });

  it("can seek and mark after reopening a cached draft in Strict Mode", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`, true);
    await user.click(await screen.findByRole("button", { name: "返回录制列表" }));
    await user.click(await screen.findByText(recording.file_name!));
    await markFirstSlice(user);
    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledOnce());
    expect(vi.mocked(gateway.saveDraft).mock.calls[0]?.[2].slices[0]).toMatchObject({
      start_offset_ns: "0", end_offset_ns: "100000000",
    });
  });

  it("creates an I/O slice and saves canonical nanosecond boundaries", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings/recording_three_hours/slice");

    expect(
      await screen.findByRole("combobox", { name: "显示视频" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /开始标记/ }));
    const sharedPlayhead = screen.getByRole("slider", {
      name: "共享播放位置",
    });
    for (let step = 0; step < 10; step += 1) {
      fireEvent.keyDown(sharedPlayhead, { key: "ArrowRight" });
    }
    await user.click(screen.getByRole("button", { name: /设为出点并添加/ }));
    expect(screen.getAllByText("Episode 0001").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(gateway.saveDraft).mock.calls[0]?.[2]).toEqual({
      slices: [
        {
          episode_id: "episode_0001",
          start_offset_ns: "0",
          end_offset_ns: "1000000000",
          title: "Episode 0001",
          task_labels: [],
        },
      ],
    });
  });
  it("saves an empty draft without requiring markers or labels", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await user.click(await screen.findByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(gateway.saveDraft).mock.calls[0]?.[2]).toEqual({
      slices: [],
    });
  });

  it("starts with a full episode description, preserves multiple labels when split, and saves edited bounds", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    const description =
      "抓取桌面物体，放入容器，再将机械臂恢复到初始位置。".repeat(20);
    vi.mocked(gateway.detail).mockResolvedValue({
      data: { ...recording, default_task_labels: [description] },
      current_slice_revision: null,
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await user.click(await screen.findByRole("button", { name: "编辑 Episode 1" }));
    expect(await screen.findByLabelText("任务描述标签 1")).toHaveValue(
      description,
    );
    expect(screen.getByText("尚未保存")).toBeInTheDocument();
    expect(screen.queryByText("已保存 r0")).not.toBeInTheDocument();
    expect(
      Number(
        (
          screen.getByRole("spinbutton", {
            name: "开始（秒）",
          }) as HTMLInputElement
        ).value,
      ),
    ).toBe(0);
    expect(
      Number(
        (
          screen.getByRole("spinbutton", {
            name: "结束（秒）",
          }) as HTMLInputElement
        ).value,
      ),
    ).toBe(10800);
    await user.click(screen.getByRole("button", { name: "添加任务描述标签" }));
    fireEvent.change(screen.getByLabelText("任务描述标签 2"), {
      target: { value: "保持物体直立" },
    });
    await user.click(screen.getByRole("button", { name: "返回切片" }));
    const playhead = screen.getByRole("slider", { name: "共享播放位置" });
    for (let step = 0; step < 10; step++)
      fireEvent.keyDown(playhead, { key: "ArrowRight" });
    await user.click(screen.getByRole("button", { name: "播放头拆分" }));
    await user.click(screen.getByRole("button", { name: "编辑 Episode 2" }));
    expect(screen.getByLabelText("任务描述标签 1")).toHaveValue(description);
    expect(screen.getByLabelText("任务描述标签 2")).toHaveValue("保持物体直立");
    await user.click(screen.getByRole("button", { name: "返回切片" }));
    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("已保存 r1")).toBeInTheDocument();
    const saved = vi.mocked(gateway.saveDraft).mock.calls[0]![2].slices;
    expect(saved.map((slice) => slice.task_labels)).toEqual([
      [description, "保持物体直立"],
      [description, "保持物体直立"],
    ]);
    expect(
      saved.map((slice) => [slice.start_offset_ns, slice.end_offset_ns]),
    ).toEqual([
      ["0", "1000000000"],
      ["1000000000", recording.duration_ns],
    ]);
  });

  it("keeps manual splits compact and deletes their rows without saving until requested", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    vi.mocked(gateway.detail).mockResolvedValue({
      data: { ...recording, default_task_labels: ["任务描述".repeat(200)] },
      current_slice_revision: null,
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await screen.findByRole("button", { name: "编辑 Episode 1" });
    expect(screen.queryByLabelText("任务描述标签 1")).not.toBeInTheDocument();
    const playhead = screen.getByRole("slider", { name: "共享播放位置" });
    for (let split = 0; split < 2; split++) {
      for (let step = 0; step < 10; step++)
        fireEvent.keyDown(playhead, { key: "ArrowRight" });
      await user.click(screen.getByRole("button", { name: "播放头拆分" }));
    }
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑 Episode 3" }));
    fireEvent.keyDown(screen.getByLabelText("任务描述标签 1"), { key: "Delete" });
    expect(screen.getAllByRole("button", { name: /^删除 Episode \d+$/ })).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "返回切片" }));

    await user.click(screen.getByRole("button", { name: "删除 Episode 1" }));
    await user.click(screen.getByRole("button", { name: "编辑 Episode 2" }));
    expect(
      Number((screen.getByRole("spinbutton", { name: "开始（秒）" }) as HTMLInputElement).value),
    ).toBe(2);
    await user.click(screen.getByRole("button", { name: "删除此片段" }));
    await user.click(screen.getByRole("button", { name: "编辑 Episode 1" }));
    expect(
      Number((screen.getByRole("spinbutton", { name: "开始（秒）" }) as HTMLInputElement).value),
    ).toBe(1);
    await user.click(screen.getByRole("button", { name: "返回切片" }));
    await user.click(screen.getByRole("button", { name: "删除 Episode 1" }));
    expect(screen.getByText("还没有 Episode")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除选中的 Episode" })).not.toBeInTheDocument();
    expect(gateway.saveDraft).not.toHaveBeenCalled();
    expect(gateway.finalize).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(gateway.saveDraft).mock.calls[0]![2].slices).toEqual([]);
  });

  it("preserves explicit label deletion after saving and reopening a draft", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    const draft = await gateway.saveDraft(
      scope,
      recording.recording_id,
      {
        slices: [
          {
            episode_id: "episode_0001",
            start_offset_ns: "0",
            end_offset_ns: "1000000000",
            task_labels: [],
          },
        ],
      },
      recording.etag,
    );
    vi.mocked(gateway.saveDraft).mockClear();
    vi.mocked(gateway.detail).mockResolvedValue({
      data: { ...draft.recording, default_task_labels: ["任务原描述"] },
      current_slice_revision: draft.revision,
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await user.click(await screen.findByRole("button", { name: "编辑 Episode 1" }));
    await screen.findByRole("button", { name: "添加任务描述标签" });
    expect(screen.queryByLabelText("任务描述标签 1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回切片" }));
    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(
      vi.mocked(gateway.saveDraft).mock.calls[0]![2].slices[0]?.task_labels,
    ).toEqual([]);
  });

  it("creates a second Episode independently of the first", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    const mark = await screen.findByRole("button", { name: /开始标记/ });
    const playhead = screen.getByRole("slider", { name: "共享播放位置" });
    await user.click(mark);
    for (let step = 0; step < 10; step++)
      fireEvent.keyDown(playhead, { key: "ArrowRight" });
    await user.click(screen.getByRole("button", { name: /设为出点并添加/ }));
    await user.click(screen.getByRole("button", { name: "新增 Episode" }));
    await user.click(mark);
    for (let step = 0; step < 10; step++)
      fireEvent.keyDown(playhead, { key: "ArrowRight" });
    await user.click(screen.getByRole("button", { name: /设为出点并添加/ }));
    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(gateway.saveDraft).toHaveBeenCalledTimes(1));
    expect(
      vi
        .mocked(gateway.saveDraft)
        .mock.calls[0]?.[2].slices.map((item) => item.episode_id),
    ).toEqual(["episode_0001", "episode_0002"]);
  });

  it("shows RGB and depth together with the annotation camera selector and one timeline", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    const envelope = await gateway.videoSources(scope, recording.recording_id);
    vi.mocked(gateway.videoSources).mockResolvedValue({
      ...envelope,
      sources: [
        ...envelope.sources,
        {
          ...envelope.sources[0]!,
          camera_id: "front_depth",
          asset_id: "depth",
          modality: "depth",
          materialization: "DEPTH_PREVIEW",
          source_url: "https://objects.example.test/front-depth-preview.mp4",
        },
      ],
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    const select = await screen.findByRole("combobox", { name: "显示视频" });
    expect(select).toHaveValue("__quad__");
    expect(await screen.findByLabelText("front 媒体")).toBeVisible();
    expect(screen.getByLabelText("front_depth 媒体")).toBeVisible();
    expect(
      screen.getAllByRole("slider", { name: "共享播放位置" }),
    ).toHaveLength(1);

    const playhead = screen.getByRole("slider", { name: "共享播放位置" });
    fireEvent.keyDown(playhead, { key: "ArrowRight" });
    const position = playhead.getAttribute("aria-valuenow");
    await user.selectOptions(select, "depth");
    expect(screen.getByLabelText("front_depth 媒体")).toBeVisible();
    expect(screen.queryByLabelText("front 媒体")).not.toBeInTheDocument();
    expect(playhead).toHaveAttribute("aria-valuenow", position);

    await user.selectOptions(select, "__all__");
    expect(screen.getByLabelText("front 媒体")).toBeVisible();
    expect(screen.getByLabelText("front_depth 媒体")).toBeVisible();
    await markFirstSlice(user);
    await user.click(screen.getByRole("button", { name: "预览 Episode 1" }));
    const preview = within(await screen.findByRole("dialog"));
    const previewSelect = preview.getByRole("combobox", { name: "显示视频" });
    expect(previewSelect).toHaveValue("__quad__");
    await waitFor(() =>
      expect(preview.getByLabelText("front 媒体")).toBeVisible(),
    );
    expect(preview.getByLabelText("front_depth 媒体")).toBeVisible();
    expect(
      preview.getAllByRole("slider", { name: "共享播放位置" }),
    ).toHaveLength(1);
    await user.selectOptions(previewSelect, "depth");
    expect(preview.getByLabelText("front_depth 媒体")).toBeVisible();
    expect(preview.queryByLabelText("front 媒体")).not.toBeInTheDocument();
  });
  it("generates the whole recording without marks and finalizes the saved ETag", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    vi.mocked(gateway.finalize).mockImplementation(async () => {
      const saved = await vi.mocked(gateway.saveDraft).mock.results[0]!.value;
      return {
        recording: {
          ...saved.recording,
          status: "SLICED" as const,
          finalized_revision: 1,
        },
        revision: { ...saved.revision, status: "FINALIZED" as const },
      };
    });
    renderPage(gateway, "/recordings/recording_three_hours/slice");
    await user.click(await screen.findByRole("button", { name: "提交成片" }));
    await user.click(await screen.findByRole("button", { name: "确认生成" }));
    await waitFor(() =>
      expect(gateway.finalize).toHaveBeenCalledWith(
        scope,
        recording.recording_id,
        1,
        '"v2"',
      ),
    );
    expect(vi.mocked(gateway.saveDraft).mock.calls[0]?.[2].slices).toEqual([
      {
        episode_id: "episode_0001",
        start_offset_ns: "0",
        end_offset_ns: recording.duration_ns,
        title: "完整录制",
        task_labels: [],
      },
    ]);
    await user.click(
      await screen.findByRole("button", { name: "关闭提交提示" }),
    );
    expect(screen.queryByText("切片已最终提交")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交成片" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "删除选中的 Episode" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除 Episode 1" })).toBeDisabled();
    expect(gateway.processing).not.toHaveBeenCalled();
  });
});


describe("ingest before interval authoring", () => {
  function wholeGateway(status: "ALIGNING" | "READY" | "FAILED", annotationTask: string | null = "task-full") {
    const gateway = makeGateway();
    const source: ContinuousRecording = { ...recording, ingest_mode: "WHOLE_RECORDING",
      status: "SLICED", current_revision: 1, finalized_revision: 1, whole_episode_status: status };
    vi.mocked(gateway.detail).mockResolvedValue({ data: source, current_slice_revision: null });
    vi.mocked(gateway.list).mockResolvedValue({ items: [source], total: 1 });
    vi.mocked(gateway.processing).mockResolvedValue({ items: [{
      schema_version: "recording-episode/v1", scope: recording.scope, recording_id: recording.recording_id,
      episode_id: "episode_full_recording", finalized_revision: 1, start_offset_ns: "0", end_offset_ns: recording.duration_ns,
      status, dataset_id: status === "READY" ? "dataset-full" : null,
      annotation_task_id: status === "READY" ? annotationTask : null,
    }], total: 1, qc_reports: [] });
    return gateway;
  }

  it("waits for the data receipt and never offers pre-ingest slicing", async () => {
    const gateway = wholeGateway("ALIGNING");
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    expect(within(await screen.findByRole("dialog")).getByText("正在对齐入库")).toBeVisible();
    expect(screen.queryByRole("button", { name: "提交成片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "进入切片与标注" })).not.toBeInTheDocument();
    expect(gateway.saveDraft).not.toHaveBeenCalled();
    expect(gateway.finalize).not.toHaveBeenCalled();
  });

  it.each(["saved URL", "queue"])("opens the annotation workbench directly from %s", async entry => {
    const gateway = wholeGateway("READY");
    renderPage(gateway, entry === "queue" ? "/recordings" : `/recordings/${recording.recording_id}/slice`);
    if (entry === "queue") {
      await userEvent.setup().click(await screen.findByRole("button", { name: "切片与标注" }));
    }
    expect(await screen.findByRole("heading", { name: "标注时间线" })).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("/annotations/tasks/task-full");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(gateway.finalize).not.toHaveBeenCalled();
  });

  it("keeps an unconfigured annotation separate from successful ingestion", async () => {
    const gateway = wholeGateway("READY", null);
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    expect(await screen.findByText("数据已入库，标注任务待准备")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看已入库数据" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "进入切片与标注" })).not.toBeInTheDocument();
  });

  it("shows failed ingestion without offering annotation", async () => {
    const gateway = wholeGateway("FAILED");
    renderPage(gateway, `/recordings/${recording.recording_id}/slice`);
    expect(within(await screen.findByRole("dialog")).getByText("整段入库失败")).toBeVisible();
    expect(screen.queryByRole("link", { name: "进入切片与标注" })).not.toBeInTheDocument();
  });

  it("keeps processing recordings out of the ready-to-author queue", async () => {
    const gateway = wholeGateway("ALIGNING");
    renderPage(gateway, "/recordings?stage=INGESTING");
    expect(await screen.findByRole("link", { name: /^整段入库，.*1 项/u })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "查看入库进度" })).toBeVisible();
  });
});
