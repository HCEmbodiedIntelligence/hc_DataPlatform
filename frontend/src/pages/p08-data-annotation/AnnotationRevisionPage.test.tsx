// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AnnotationRevisionPage } from "./AnnotationRevisionPage";

const { grantedCapabilities, listThreadsMock } = vi.hoisted(() => ({
  grantedCapabilities: new Set<string>(),
  listThreadsMock: vi.fn(),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => grantedCapabilities.has(capability),
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../shared/scope/shell-store", () => ({
  useShellStore: (selector: (state: object) => unknown) =>
    selector({
      scope: {
        organizationId: "org-revision",
        projectId: "project-revision",
        regionCode: "cn-hz",
      },
    }),
}));

vi.mock("./runtime-annotation-adapter", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("./runtime-annotation-adapter")>();
  return {
    ...actual,
    listRuntimeAnnotationRevisionThreads: listThreadsMock,
  };
});

function page(
  taskId: string,
  options: { readonly next?: string | null; readonly status?: string } = {},
) {
  return {
    items: [
      {
        task_id: taskId,
        project_id: "project-revision",
        region_code: "cn-hz",
        dataset_id: `dataset-${taskId}`,
        dataset_version: 3,
        rollout_id: `rollout-${taskId}`,
        status: options.status ?? "DRAFT",
        latest_revision: {
          revision: 2,
          origin: "ANNOTATION",
          author_id: "annotator",
          content_hash: "a".repeat(64),
          created_at: "2026-08-20T08:00:00Z",
        },
        submitted_revision: null,
        current_submission_id: null,
        approved_revision: null,
        approved_review_id: null,
        updated_at: "2026-08-20T08:01:00Z",
      },
    ],
    page_info: {
      has_next_page: options.next !== undefined && options.next !== null,
      has_previous_page: false,
      start_cursor: null,
      end_cursor: options.next ?? null,
    },
    snapshot_at: "2026-08-20T08:02:00Z",
  };
}

function renderPage(path = "/annotations/revisions") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AnnotationRevisionPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  grantedCapabilities.clear();
  grantedCapabilities.add("annotation_task.read");
  grantedCapabilities.add("episode.read");
  listThreadsMock.mockReset();
  listThreadsMock.mockResolvedValue(page("task-1", { next: "cursor-1" }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnnotationRevisionPage", () => {
  it("loads the bounded revision index, exposes exact task navigation, and fetches only the next cursor page", async () => {
    listThreadsMock
      .mockResolvedValueOnce(page("task-1", { next: "cursor-1" }))
      .mockResolvedValueOnce(page("task-2"));
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Episode 版本与草稿修订" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "返回任务队列" })).toHaveAttribute(
      "href",
      "/annotations/annotate",
    );
    expect(screen.getByText("rollout-task-1")).toBeVisible();
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveAttribute(
      "href",
      "/annotations/tasks/task-1",
    );
    expect(listThreadsMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "加载下一页" }));
    await waitFor(() => expect(listThreadsMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("rollout-task-2")).toBeVisible();
    expect(listThreadsMock.mock.calls[1]?.[1]).toMatchObject({
      after: "cursor-1",
    });
  });

  it("refetches from the first page when the server-side status filter changes", async () => {
    renderPage();
    await screen.findByText("rollout-task-1");

    fireEvent.change(screen.getByLabelText("工作流状态"), {
      target: { value: "SUBMITTED" },
    });
    await waitFor(() => expect(listThreadsMock).toHaveBeenCalledTimes(2));
    expect(listThreadsMock.mock.calls[1]?.[1]).toMatchObject({
      status: "SUBMITTED",
    });
    expect(listThreadsMock.mock.calls[1]?.[1]).not.toHaveProperty("after");
  });

  it("does not issue a request without the annotation read capability", async () => {
    grantedCapabilities.clear();
    renderPage();

    expect(
      await screen.findByText("你没有访问此标注资源的权限。"),
    ).toBeVisible();
    expect(listThreadsMock).not.toHaveBeenCalled();
  });

});
