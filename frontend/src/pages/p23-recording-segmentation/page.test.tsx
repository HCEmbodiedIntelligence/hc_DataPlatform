// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
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

const scope = {
  organizationId: "org-test",
  projectId: "project-test",
  regionCode: "cn-test",
} as const;

const recording: ContinuousRecording = {
  schema_version: "continuous-recording/v2",
  scope: {
    organization_id: scope.organizationId,
    project_id: scope.projectId,
    region_code: scope.regionCode,
  },
  recording_id: "recording_three_hours",
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
    processing: vi.fn(async () => ({ items: [], total: 0 })),
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

function renderPage(gateway: RecordingGateway, initialEntry: string) {
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
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </ProviderHarness>,
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
      within(stages).getByRole("link", { name: /^待分割，.*1 项/u }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(stages).getByRole("link", { name: /^待检查，.*1 项/u }),
    ).toHaveAttribute("href", "/recordings?stage=REVIEW");
    expect(
      within(stages).getByRole("link", { name: /^已分割，.*1 项/u }),
    ).toHaveAttribute("href", "/recordings?stage=SLICED");
  });

  it("opens the manual cutter from the continuous-recording list", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings");

    expect(
      await screen.findByRole("heading", { name: "录制切片" }),
    ).toBeVisible();
    expect(await screen.findByText(recording.recording_id)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "开始切片" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/recordings/recording_three_hours/slice",
    );
  });

  it("creates an I/O slice and saves canonical nanosecond boundaries", async () => {
    const gateway = makeGateway();
    const user = userEvent.setup();
    renderPage(gateway, "/recordings/recording_three_hours/slice");

    expect(
      await screen.findByText("直接读取 OSS，不生成预览副本"),
    ).toBeVisible();
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
        },
      ],
    });
  });
});
