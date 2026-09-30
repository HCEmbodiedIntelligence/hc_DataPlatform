// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { TagEpisodePanel } from "./TagEpisodePanel";
import {
  commandTagEpisode,
  createTagEpisode,
  listTagEpisodes,
  type TagEpisodeJob,
} from "./tag-episodes";
import { tagEpisodeFixture } from "./testing/tag-episode-fixture";
vi.mock("./tag-episodes", () => ({
  commandTagEpisode: vi.fn(),
  createTagEpisode: vi.fn(),
  listTagEpisodes: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(
  changes: Partial<TagEpisodeJob> = {},
  canCreate = true,
  canRead = true,
) {
  const f = tagEpisodeFixture();
  const job = { ...f.job, ...changes };
  vi.mocked(listTagEpisodes).mockResolvedValue([job]);
  vi.mocked(createTagEpisode).mockResolvedValue(job);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <TagEpisodePanel
        scope={f.scope}
        history={f.history}
        dirty
        canCreate={canCreate}
        canRead={canRead}
      />
    </QueryClientProvider>,
  );
  return { ...f, job, ...view };
}
async function selectTag() {
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "来源 Tag" }),
    "first",
  );
  await userEvent.click(
    screen.getByRole("checkbox", { name: /明确使用未审核修订/ }),
  );
}
it("requires one saved Tag and explicit unreviewed choice, while displaying depth and independent reviews", async () => {
  const f = setup();
  const create = screen.getByRole("button", { name: "创建独立 Episode" });
  expect(create).toBeDisabled();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "来源 Tag" }),
    "first",
  );
  expect(create).toBeDisabled();
  await userEvent.click(
    screen.getByRole("checkbox", { name: /明确使用未审核修订/ }),
  );
  await userEvent.click(create);
  expect(createTagEpisode).toHaveBeenCalledWith(
    f.scope,
    f.history,
    {
      revision: f.revision.revision,
      annotation_id: "first",
      allow_unreviewed: true,
    },
    expect.any(String),
    expect.any(AbortSignal),
  );
  expect(
    screen.getByText(/已采集深度视频、profile 和有效性掩码/),
  ).toBeVisible();
  expect(screen.getByText(/不继承父条目的通过状态/)).toBeVisible();
  expect(screen.getByText(/未保存的修改不包含/)).toBeVisible();
  const link = await screen.findByRole("link", {
    name: "打开新标注任务（新窗口）",
  });
  expect(link).toHaveAttribute("href", "/annotations/tasks/annotation-child");
  expect(link).toHaveAttribute("target", "_blank");
});
it("keeps the same idempotency key across an ambiguous response and changes it for a new selection", async () => {
  setup();
  vi.mocked(createTagEpisode).mockRejectedValueOnce(new Error("连接中断"));
  await selectTag();
  const create = screen.getByRole("button", { name: "创建独立 Episode" });
  await userEvent.click(create);
  await screen.findByText("连接中断");
  await waitFor(() => expect(create).toBeEnabled());
  await userEvent.click(create);
  await waitFor(() => expect(create).toBeEnabled());
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "来源 Tag" }),
    "cross",
  );
  await userEvent.click(create);
  const calls = vi.mocked(createTagEpisode).mock.calls;
  expect(calls[0]![3]).toBe(calls[1]![3]);
  expect(calls[2]![3]).not.toBe(calls[1]![3]);
});
it("recovers already committed data without offering cancellation", async () => {
  const f = setup({
    status: "FAILED",
    phase: "failed",
    error_code: "TAG_EPISODE_PROCESSING_FAILED",
    viewer_target: null,
    collection_version_id: null,
    annotation_task_id: null,
  });
  expect(await screen.findByText(/独立数据包已保存/)).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "重试创建同一 Episode" }),
  );
  expect(commandTagEpisode).toHaveBeenCalledWith(
    f.scope,
    f.history,
    f.job,
    "retry",
    expect.any(AbortSignal),
  );
  expect(
    screen.queryByRole("button", { name: "取消尚未开始的创建" }),
  ).not.toBeInTheDocument();
});
it.each([0, 1])(
  "only offers pending cancellation before any attempt (attempts=%s)",
  async (attempts) => {
    setup({
      status: "PENDING",
      phase: "queued",
      attempts,
      committed_data: null,
      collection_version_id: null,
      viewer_target: null,
      annotation_task_id: null,
    });
    await screen.findByText(/等待创建/);
    expect(
      Boolean(screen.queryByRole("button", { name: "取消尚未开始的创建" })),
    ).toBe(attempts === 0);
  },
);
it("disables primary creation without upload.manage and does not fetch without source-read permissions", async () => {
  setup({}, false, false);
  await selectTag();
  expect(
    screen.getByRole("button", { name: "创建独立 Episode" }),
  ).toBeDisabled();
  expect(listTagEpisodes).not.toHaveBeenCalled();
  expect(createTagEpisode).not.toHaveBeenCalled();
});
