// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import {
  applyRuntimeAutoAnnotationJob,
  createRuntimeAutoAnnotationJob,
  getRuntimeAutoAnnotationJob,
  loadRuntimeAutoAnnotationCapability,
} from "./runtime-annotation-adapter";
import { RuntimeAutoAnnotationPanel } from "./RuntimeAutoAnnotationPanel";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";
import type { RuntimeAutoAnnotationJob } from "./runtime-annotation-adapter";

vi.mock("./runtime-annotation-adapter", () => ({
  applyRuntimeAutoAnnotationJob: vi.fn(),
  cancelRuntimeAutoAnnotationJob: vi.fn(),
  createRuntimeAutoAnnotationJob: vi.fn(),
  getRuntimeAutoAnnotationJob: vi.fn(),
  loadRuntimeAutoAnnotationCapability: vi.fn(),
  retryRuntimeAutoAnnotationJob: vi.fn(),
}));

const capabilityMock = vi.mocked(loadRuntimeAutoAnnotationCapability);
const createMock = vi.mocked(createRuntimeAutoAnnotationJob);
const getMock = vi.mocked(getRuntimeAutoAnnotationJob);
const applyMock = vi.mocked(applyRuntimeAutoAnnotationJob);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前测试 URL">{location.search}</output>;
}

function renderPanel(initialEntry = "/annotations/tasks/task-1") {
  const bundle = createVisualAnnotationBundle({ mode: "annotation" });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onApplied = vi.fn<() => Promise<void>>().mockResolvedValue();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <RuntimeAutoAnnotationPanel
          canUse
          dirty={false}
          scope={visualAnnotationScope}
          task={bundle.task}
          onApplied={onApplied}
        />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { bundle, onApplied };
}

function job(
  taskId: string,
  status: "QUEUED" | "SUCCEEDED" | "APPLIED",
): RuntimeAutoAnnotationJob {
  const fixture = createVisualAnnotationBundle({ mode: "annotation" });
  const successful = status === "SUCCEEDED" || status === "APPLIED";
  return {
    job_id: "71b98020-d722-4b3b-960c-65bb1fd89ed1",
    project_id: visualAnnotationScope.projectId,
    region_code: visualAnnotationScope.regionCode,
    task_id: taskId,
    source_revision: fixture.task.current_revision,
    provider: "vlm",
    model: "vision-v1",
    input_selection: { start_step: 0, end_step: 600, modalities: [] },
    status,
    progress_percent: status === "QUEUED" ? 0 : 100,
    estimated_cost_micros: 1_000,
    ...(successful
      ? {
          tags: fixture.draft!.tags,
          operations: fixture.draft!.operations,
          usage: { input_units: 600, output_units: 2, cost_micros: 900 },
        }
      : {}),
    ...(status === "APPLIED" ? { applied_revision: 2 } : {}),
    created_by: "annotator",
    created_at: "2026-08-24T03:00:00Z",
    updated_at: "2026-08-24T03:00:01Z",
  };
}

describe("P08 runtime automatic annotation panel", () => {
  it("shows a real provider-unavailable state and never creates a fake job", async () => {
    capabilityMock.mockResolvedValue({
      enabled: false,
      code: "PROVIDER_UNAVAILABLE",
      providers: [],
      max_concurrent_jobs_per_project: 4,
      max_jobs_per_hour: 60,
      daily_cost_limit_micros: 5_000_000,
    });

    renderPanel();

    expect(await screen.findByText(/不会创建假任务或假结果/u)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "启动自动标注" }),
    ).not.toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("starts a durable job, preserves its ID in the URL, and applies a confirmed result", async () => {
    const user = userEvent.setup();
    capabilityMock.mockResolvedValue({
      enabled: true,
      code: null,
      providers: [{ provider: "vlm", models: ["vision-v1"] }],
      max_concurrent_jobs_per_project: 4,
      max_jobs_per_hour: 60,
      daily_cost_limit_micros: 5_000_000,
    });
    const { bundle, onApplied } = renderPanel();
    const queued = job(bundle.task.task_id, "QUEUED");
    const succeeded = job(bundle.task.task_id, "SUCCEEDED");
    const applied = job(bundle.task.task_id, "APPLIED");
    createMock.mockResolvedValue(queued);
    getMock.mockResolvedValueOnce(succeeded).mockResolvedValueOnce(applied);
    applyMock.mockResolvedValue({
      ...bundle.history.revisions.at(-1)!,
      parent_revision: bundle.task.current_revision,
      revision: bundle.task.current_revision + 1,
    });

    await screen.findByRole("button", { name: "启动自动标注" });
    await user.click(screen.getByRole("button", { name: "启动自动标注" }));

    await waitFor(() =>
      expect(screen.getByLabelText("当前测试 URL")).toHaveTextContent(
        `autoJobId=${queued.job_id}`,
      ),
    );
    expect(createMock).toHaveBeenCalledWith(
      visualAnnotationScope,
      bundle.task,
      expect.objectContaining({
        provider: "vlm",
        model: "vision-v1",
        startStep: 0,
      }),
    );
    expect(await screen.findByText("结果待确认")).toBeVisible();
    expect(screen.getByText(/1 Tag/u)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "应用为新修订" }));
    await user.click(
      within(
        screen.getByRole("dialog", { name: "应用自动标注结果" }),
      ).getByRole("button", { name: "确认应用自动标注" }),
    );

    await waitFor(() => expect(applyMock).toHaveBeenCalledOnce());
    expect(onApplied).toHaveBeenCalledOnce();
    expect(await screen.findByText("已追加为修订")).toBeVisible();
  });

  it("rejects a malformed URL job ID without a server job request", async () => {
    capabilityMock.mockResolvedValue({
      enabled: true,
      code: null,
      providers: [{ provider: "vlm", models: ["vision-v1"] }],
      max_concurrent_jobs_per_project: 4,
      max_jobs_per_hour: 60,
      daily_cost_limit_micros: 5_000_000,
    });

    renderPanel("/annotations/tasks/task-1?autoJobId=../../foreign");

    expect(await screen.findByText(/任务 ID 非法/u)).toBeVisible();
    expect(getMock).not.toHaveBeenCalled();
  });
});
