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
        legacy_draft_id: null,
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
      await screen.findByRole("heading", { name: "数据修订" }),
    ).toBeVisible();
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

  it("keeps an imported legacy revision inside the annotation task workflow", async () => {
    const legacyPage = page("task-legacy", { next: null });
    const [legacyThread] = legacyPage.items;
    if (!legacyThread)
      throw new Error("legacy test fixture must include one thread");
    listThreadsMock.mockImplementation(async () => ({
      ...legacyPage,
      items: [
        {
          ...legacyThread,
          legacy_draft_id: "draft_legacy-01",
          latest_revision: {
            ...legacyThread.latest_revision,
            origin: "LEGACY_CLEANING",
          },
        },
      ],
    }));
    renderPage();

    expect(
      await screen.findByText("已关联旧草稿 draft_legacy-01"),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveAttribute(
      "href",
      "/annotations/tasks/task-legacy",
    );
    expect(screen.queryByText("打开历史清洗草稿")).not.toBeInTheDocument();
  });

  it("resolves a legacy route only through its exact scoped annotation mapping", async () => {
    const legacyPage = page("task-legacy-route", { next: null });
    const [legacyThread] = legacyPage.items;
    if (!legacyThread)
      throw new Error("legacy route fixture must include one thread");
    listThreadsMock.mockResolvedValue({
      ...legacyPage,
      items: [
        {
          ...legacyThread,
          legacy_draft_id: "draft_legacy-route",
          latest_revision: {
            ...legacyThread.latest_revision,
            origin: "LEGACY_CLEANING",
          },
        },
      ],
    });

    renderPage("/annotations/revisions?legacyDraftId=draft_legacy-route");

    expect(await screen.findByText("旧清洗草稿兼容入口")).toBeVisible();
    expect(listThreadsMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ legacyDraftId: "draft_legacy-route" }),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveAttribute(
      "href",
      "/annotations/tasks/task-legacy-route",
    );
    expect(
      screen.getByRole("link", { name: "查看全部数据修订" }),
    ).toHaveAttribute("href", "/annotations/revisions");
  });
});
