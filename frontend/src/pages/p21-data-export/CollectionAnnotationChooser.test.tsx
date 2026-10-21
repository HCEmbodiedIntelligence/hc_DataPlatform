// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { episodeDetailFixture } from "../p06-dataset-detail/testing/episode-detail-fixture";
import { loadCollectionAnnotationHistory } from "../../features/exports/package-deliveries";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";
import { CollectionAnnotationChooser } from "./CollectionAnnotationChooser";

vi.mock("../../features/datasets/episodes/episode-detail", () => ({
  loadEpisodeOverview: vi.fn(),
}));
vi.mock("../../features/exports/package-deliveries", () => ({
  loadCollectionAnnotationHistory: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});
function mount(
  options: { empty?: boolean; approved?: boolean; error?: boolean } = {},
) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
  const f = packageDeliveryFixture(true),
    overview = episodeDetailFixture().view,
    onChange = vi.fn();
  vi.mocked(loadEpisodeOverview).mockResolvedValue({
    ...overview,
    data: f.data,
    annotation: {
      ...overview.annotation,
      availability: "AVAILABLE",
      task_id: f.annotation.task.task_id,
    },
  });
  const current = f.annotation.revision;
  f.annotation.history.task.current_revision = current.revision;
  f.annotation.history.revisions.unshift({
    ...current,
    revision: current.revision - 1,
    tags: [{ ...current.tags[0]!, label: "旧标注不应出现" }],
  });
  if (options.empty) current.tags = [];
  vi.mocked(loadCollectionAnnotationHistory).mockResolvedValue({
    ...f.annotation.history,
    task: {
      ...f.annotation.history.task,
      approved_revision: options.approved
        ? current.revision
        : current.revision - 1,
    },
  });
  if (options.error)
    vi.mocked(loadCollectionAnnotationHistory).mockRejectedValue(
      new Error("读取失败"),
    );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CollectionAnnotationChooser
        scope={f.scope}
        data={f.data}
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  return { f, onChange, client };
}
it("automatically uses current unreviewed annotations without requiring segments or confirmation", async () => {
  const { f, onChange } = mount();
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith({
      task_id: f.annotation.task.task_id,
      revision: f.annotation.revision.revision,
      allow_unreviewed: true,
      track: {
        format: "hc-subtask-track/1",
        track_id: "task-segments",
        label: "任务分段",
        annotation_ids: [],
      },
    }),
  );
  expect(
    screen.queryByRole("combobox", { name: /标注修订/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "确认当前标注和分段" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("checkbox", { name: "包含尚未审核的当前标注" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("未审核 · 将按当前内容导出")).toBeVisible();
  expect(
    screen.getByText("任务分段（可选）").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.setup().click(screen.getByText("任务分段（可选）"));
  expect(screen.queryByText(/旧标注不应出现/)).not.toBeInTheDocument();
});
it("updates optional segments immediately and blocks overlapping ranges until cleared", async () => {
  const { onChange } = mount(),
    user = userEvent.setup();
  await user.click(await screen.findByText("任务分段（可选）"));
  await user.click(screen.getByRole("checkbox", { name: "第一段 [0, 2)" }));
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        allow_unreviewed: true,
        track: expect.objectContaining({ annotation_ids: ["first"] }),
      }),
    ),
  );
  await user.click(screen.getByRole("checkbox", { name: "交叉标签 [1, 4)" }));
  expect(
    screen.getByText("所选分段有重叠，请取消重叠标签，或清空分段直接导出。"),
  ).toBeVisible();
  expect(onChange).toHaveBeenLastCalledWith(null);
  await user.click(screen.getByRole("button", { name: "清空分段" }));
  expect(onChange).toHaveBeenLastCalledWith(
    expect.objectContaining({
      track: expect.objectContaining({ annotation_ids: [] }),
    }),
  );
});
it("exports a saved revision with no tags and preserves approval status", async () => {
  const { onChange } = mount({ empty: true, approved: true });
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        allow_unreviewed: false,
        track: expect.objectContaining({ annotation_ids: [] }),
      }),
    ),
  );
  expect(screen.getByText("已审核")).toBeVisible();
  expect(screen.getByText("已保存 0 个标签")).toBeVisible();
  expect(screen.queryByText("任务分段（可选）")).not.toBeInTheDocument();
});
it("blocks export on a read error and can retry", async () => {
  const { f, onChange } = mount({ error: true });
  expect(await screen.findByText("标注读取失败")).toBeVisible();
  expect(onChange).toHaveBeenLastCalledWith(null);
  vi.mocked(loadCollectionAnnotationHistory).mockResolvedValue(
    f.annotation.history,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        revision: f.annotation.revision.revision,
      }),
    ),
  );
});
it("drops old segment selections when the current saved revision changes", async () => {
  const { f, onChange, client } = mount(),
    user = userEvent.setup();
  await user.click(await screen.findByText("任务分段（可选）"));
  await user.click(screen.getByRole("checkbox", { name: "第一段 [0, 2)" }));
  const next = f.annotation.revision.revision + 1;
  vi.mocked(loadCollectionAnnotationHistory).mockResolvedValue({
    ...f.annotation.history,
    task: { ...f.annotation.history.task, current_revision: next },
    revisions: [{ ...f.annotation.revision, revision: next, tags: [] }],
  });
  await client.invalidateQueries({ queryKey: ["batch-annotation-history"] });
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        revision: next,
        track: expect.objectContaining({ annotation_ids: [] }),
      }),
    ),
  );
});
