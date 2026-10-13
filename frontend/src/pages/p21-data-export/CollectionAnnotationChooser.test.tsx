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
it("uses current annotations instead of older approval, without a revision selector", async () => {
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
  vi.mocked(loadCollectionAnnotationHistory).mockResolvedValue({
    ...f.annotation.history,
    task: {
      ...f.annotation.history.task,
      approved_revision: current.revision - 1,
    },
  });
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
  expect(
    screen.queryByRole("combobox", { name: /标注修订/ }),
  ).not.toBeInTheDocument();
  const user = userEvent.setup(),
    choose = await screen.findByRole("button", {
      name: "确认当前标注和分段",
    });
  expect(screen.queryByText(/旧标注不应出现/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("combobox", { name: /标注修订/ }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "第一段 [0, 2)" }));
  expect(choose).toBeDisabled();
  await user.click(
    screen.getByRole("checkbox", { name: "包含尚未审核的当前标注" }),
  );
  await user.click(choose);
  await waitFor(() =>
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        task_id: f.annotation.task.task_id,
        revision: f.annotation.revision.revision,
        allow_unreviewed: true,
        track: expect.objectContaining({ annotation_ids: ["first"] }),
      }),
    ),
  );
  await user.click(screen.getByRole("checkbox", { name: "交叉标签 [1, 4)" }));
  expect(screen.getByText("选定轨道的区间重叠，请调整标签选择")).toBeVisible();
  expect(choose).toBeDisabled();
  expect(onChange).toHaveBeenLastCalledWith(null);
});
