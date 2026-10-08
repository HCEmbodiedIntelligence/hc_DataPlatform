// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EpisodeDetailPanel } from "./EpisodeDetailPanel";
import {
  authorizeEpisodeFile,
  downloadAnnotationMigration,
  loadEpisodeFiles,
  loadEpisodeOverview,
} from "../../features/datasets/episodes/episode-detail";
import {
  episodeDetailFixture,
  episodeMigrationFixture,
} from "./testing/episode-detail-fixture";

vi.mock(
  "../../features/datasets/episodes/episode-detail",
  async (original) => ({
    ...(await original<
      typeof import("../../features/datasets/episodes/episode-detail")
    >()),
    loadEpisodeOverview: vi.fn(),
    loadEpisodeFiles: vi.fn(),
    authorizeEpisodeFile: vi.fn(),
    downloadAnnotationMigration: vi.fn(),
  }),
);
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

it("shows historical approval separately and saves verified mapping bytes", async () => {
  const f = episodeMigrationFixture();
  mount(true, (view) => Object.assign(view, f.view));
  await screen.findByText("抓取演示");
  await userEvent.click(screen.getByRole("tab", { name: "标注与审核" }));
  expect(
    await screen.findByText(
      "历史审核仅作来源记录；当前版本标注需独立提交和审核。",
    ),
  ).toBeVisible();
  expect(screen.getByText("尚未批准")).toBeVisible();
  expect(screen.getByText(/来源：历史数据版本 132/)).toBeVisible();
  const create = vi.fn().mockReturnValue("blob:verified-mapping");
  vi.stubGlobal(
    "URL",
    Object.assign(class {}, {
      createObjectURL: create,
      revokeObjectURL: vi.fn(),
    }),
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const bytes = new Blob(["{ }\n"]);
  vi.mocked(downloadAnnotationMigration).mockResolvedValue(bytes);
  await userEvent.click(
    screen.getByRole("button", { name: "下载历史标注映射 JSON" }),
  );
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(create).toHaveBeenCalledWith(bytes);
  expect(downloadAnnotationMigration).toHaveBeenCalledWith(
    f.scope,
    f.data,
    f.mapping,
    expect.any(AbortSignal),
  );
});

it("disables historical mapping download without permission", async () => {
  const f = episodeMigrationFixture();
  f.mapping.download.allowed = false;
  Object.assign(f.mapping.download, { denied_reason: "MISSING_CAPABILITY" });
  mount(true, (view) => Object.assign(view, f.view));
  await screen.findByText("抓取演示");
  await userEvent.click(screen.getByRole("tab", { name: "标注与审核" }));
  expect(
    await screen.findByRole("button", { name: "下载历史标注映射 JSON" }),
  ).toBeDisabled();
  expect(downloadAnnotationMigration).not.toHaveBeenCalled();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
function mount(
  canRead = true,
  adjust?: (view: ReturnType<typeof episodeDetailFixture>["view"]) => void,
) {
  const f = episodeDetailFixture(),
    close = vi.fn();
  adjust?.(f.view);
  vi.mocked(loadEpisodeOverview).mockResolvedValue(f.view);
  vi.mocked(loadEpisodeFiles).mockResolvedValue(f.view.files);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EpisodeDetailPanel
          scope={f.scope}
          data={f.data}
          canRead={canRead}
          canReview={false}
          returnToPlayback={close}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...f, close };
}
it("shows distinct statuses and authorizes a file without fetching its bytes", async () => {
  const { view, close } = mount();
  expect(await screen.findByText("抓取演示")).toBeVisible();
  expect(screen.getByText("未指定")).toBeVisible();
  await userEvent.click(screen.getByRole("tab", { name: "数据文件" }));
  expect(await screen.findByText("meta/info.json")).toBeVisible();
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  vi.mocked(authorizeEpisodeFile).mockResolvedValue({
    data: view.data,
    file_id: view.files.items[0]!.file_id,
    size: 123,
    sha256: "b".repeat(64),
    url: "http://127.0.0.1:49002/file",
    expires_seconds: 300,
  });
  await userEvent.click(screen.getByRole("button", { name: "下载文件" }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  await userEvent.click(screen.getByRole("button", { name: "返回同步回放" }));
  expect(close).toHaveBeenCalledOnce();
});
it("keeps missing provenance explicit and never loads without read permission", async () => {
  mount(false);
  expect(screen.getByText("缺少数据版本读取权限")).toBeVisible();
  expect(loadEpisodeOverview).not.toHaveBeenCalled();
  cleanup();
  mount();
  await screen.findByText("抓取演示");
  await userEvent.click(screen.getByRole("tab", { name: "版本与来源" }));
  expect(await screen.findByText("此版本未记录")).toBeVisible();
});
it("selects an explicit task without leaving the annotation tab or mixing an old review into the draft", async () => {
  const f = mount(true, (view) => {
    view.annotation.availability = "SELECTION_REQUIRED";
    view.annotation.task_options = [1, 2].map((n) => ({
      task_id: `task-${n}`,
      dataset_version: n,
      status: "DRAFT",
      current_revision: 1,
      state_version: 5,
    }));
  });
  await screen.findByText("抓取演示");
  await userEvent.click(screen.getByRole("tab", { name: "标注与审核" }));
  const selected = structuredClone(f.view);
  selected.annotation = {
    ...selected.annotation,
    availability: "AVAILABLE",
    task_options: [],
    task_id: "task-1",
    status: "DRAFT",
    current_revision: {
      task_id: "task-1",
      revision: 1,
      content_hash: "b".repeat(64),
      author_id: "editor",
      created_at: f.view.observed_at,
    },
    latest_review: {
      task_id: "task-1",
      revision: 0,
      revision_content_hash: "a".repeat(64),
      data_ref: f.data,
      submission_id: "submission-0",
      review_id: "review-0",
      decision: "NEEDS_REVISION",
    },
  };
  vi.mocked(loadEpisodeOverview).mockResolvedValue(selected);
  await userEvent.click(screen.getByRole("button", { name: /task-1/ }));
  expect(await screen.findByText("当前修订未审核")).toBeVisible();
  expect(screen.getByRole("tab", { name: "标注与审核" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByText("需返修")).toBeVisible();
  expect(loadEpisodeOverview).toHaveBeenLastCalledWith(
    f.scope,
    f.data,
    { annotationTaskId: "task-1" },
    expect.any(AbortSignal),
  );
});
