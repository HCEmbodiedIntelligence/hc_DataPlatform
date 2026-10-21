// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RobotUploadSummary } from "../../../features/robot-ingest/model";
import { RobotUploadRecordsPanel } from "./RobotUploadRecordsPanel";

const data = vi.hoisted(() => ({ items: [] as Record<string, unknown>[] }));
vi.mock("../../../features/robot-ingest/api", () => ({
  useRobotUploadEpisodes: () => ({
    data,
    isPending: false,
    isError: false,
  }),
}));

beforeEach(() => {
  data.items = [];
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
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
      dispatchEvent: vi.fn(),
    })),
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const upload: RobotUploadSummary = {
  upload_id: "riu-a",
  client_upload_id: "00000000-0000-4000-8000-000000000001",
  authenticated_robot_id: "robot-a",
  request_robot_id: "robot-a",
  collection_job_id: "job-a",
  capture_mode: "CONTINUOUS",
  source_format: "CAPTURE_BUNDLE",
  source_format_version: "2",
  declared_episode_count: null,
  verified_episode_count: null,
  derived_episode_count: 12,
  verified_frame_count: 21_600,
  verified_sample_count: 720_000,
  qc_pass_episode_count: 11,
  qc_risk_episode_count: 1,
  qc_reject_episode_count: 0,
  total_bytes: 1024,
  state: "COMMITTED",
  processing_status: "READY",
  quality_status: "RISK",
  target: {
    collection_task_id: "task-a",
    organization_id: "org-a",
    project_id: "project-a",
    dataset_id: "dataset-a",
    region_code: "cn-east-1",
  },
  raw_source_id: "raw-a",
  created_at: "2026-09-01T00:00:00Z",
  expires_at: "2026-09-08T00:00:00Z",
  updated_at: "2026-09-01T02:00:00Z",
  committed_at: "2026-09-01T02:00:00Z",
};

describe("P03 robot upload records", () => {
  it("shows Raw processing and quality states separately", () => {
    render(
      <RobotUploadRecordsPanel
        scope={{
          organizationId: "org-a",
          projectId: "project-a",
          regionCode: "cn-east-1",
        }}
        robotId="robot-a"
        items={[upload]}
        loading={false}
        errorMessage={null}
        onClear={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("raw-a")).toBeVisible();
    expect(screen.getByText("READY")).toBeVisible();
    expect(screen.getByText("RISK")).toBeVisible();
    expect(screen.getByText(/派生 12/u)).toBeVisible();
  });
});

it("keeps READY data separate from annotation pending configuration", () => {
  data.items = [
    {
      episode_id: "episode",
      status: "READY",
      annotation_status: "PENDING_CONFIGURATION",
      data_readable: true,
      frame_count: 167,
      sample_count: 0,
      quality_status: "PASS",
    },
  ];
  const { container } = render(
    <RobotUploadRecordsPanel
      scope={{ organizationId: "org", projectId: "p", regionCode: "global" }}
      robotId="r"
      items={[upload]}
      loading={false}
      errorMessage={null}
      onClear={vi.fn()}
      onRefresh={vi.fn()}
    />,
  );
  fireEvent.click(container.querySelector(".ant-table-row-expand-icon")!);
  expect(screen.getByText("标注待配置")).toBeVisible();
  expect(screen.getByText("可读取")).toBeVisible();
  expect(screen.getByText("167 / 0")).toBeVisible();
  expect(screen.getAllByText("READY")).toHaveLength(2);
});
