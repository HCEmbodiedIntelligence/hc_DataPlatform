// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { AnnotationQueuePage, TagReviewQueuePage } from "./AnnotationQueuePage";
import { annotationQueueQueryCodec } from "./query-codec";
import type { AnnotationQueueStage } from "./query-codec";
import type { RuntimeAnnotationTask } from "../../features/annotation/runtime/runtime-annotation-adapter";

const { grantedCapabilities, listTasksMock, claimTaskMock } = vi.hoisted(
  () => ({
    grantedCapabilities: new Set<string>(),
    listTasksMock: vi.fn(),
    claimTaskMock: vi.fn(),
  }),
);

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
      principal: {
        actorId: "annotator-current",
        displayName: "当前标注员",
        roleIds: [],
      },
      scope: {
        organizationId: "org-fe13",
        projectId: "project-fe13",
        regionCode: "cn-shanghai",
      },
    }),
}));

vi.mock(
  "../../features/annotation/runtime/runtime-annotation-adapter",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../features/annotation/runtime/runtime-annotation-adapter")
      >();
    return {
      ...actual,
      listRuntimeAnnotationTasks: listTasksMock,
      claimRuntimeAnnotationTask: claimTaskMock,
    };
  },
);

function task(
  status: AnnotationQueueStage,
  overrides: Partial<RuntimeAnnotationTask> = {},
): RuntimeAnnotationTask {
  const slug = status.toLocaleLowerCase("en-US");
  const submitted = status === "SUBMITTED";
  return {
    task_id: `task-${slug}`,
    project_id: "project-fe13",
    region_code: "cn-shanghai",
    dataset_id: `dataset-${slug}`,
    dataset_version: 8,
    rollout_id: `rollout-${slug}`,
    base_lance_version: 12,
    base_step_count: 900,
    tag_schema_id: `schema-${slug}`,
    tag_schema_version: 3,
    task_kind: "TAGGING",
    creation_source: "SYSTEM_LANCE",
    source_workflow_id: `ingest/${slug}`,
    assignee_id: status === "DRAFT" ? null : "annotator-current",
    current_revision: submitted ? 2 : 1,
    state_version: 4,
    current_submission_id: submitted ? `submission-${slug}` : null,
    submitted_revision: submitted ? 2 : null,
    submitted_by: submitted ? "annotator-other" : null,
    approved_revision: status === "APPROVED" ? 1 : null,
    approved_review_id: status === "APPROVED" ? `review-${slug}` : null,
    status,
    schema_version: "1",
    etag: `"${slug}-v4"`,
    created_at: "2026-08-20T08:00:00Z",
    updated_at: "2026-08-24T09:30:00Z",
    ...overrides,
  };
}

function CurrentLocation() {
  const location = useLocation();
  return (
    <output aria-label="当前位置">{location.pathname + location.search}</output>
  );
}

function renderQueue(
  mode: "annotation" | "tag-review",
  path = mode === "annotation"
    ? "/annotations/annotate"
    : "/annotations/tag-review",
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Component =
    mode === "annotation" ? AnnotationQueuePage : TagReviewQueuePage;
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Component />
        <CurrentLocation />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
  grantedCapabilities.clear();
  grantedCapabilities.add("annotation_task.read");
  grantedCapabilities.add("episode.read");
  listTasksMock.mockReset();
  listTasksMock.mockResolvedValue([]);
  claimTaskMock.mockReset();
  claimTaskMock.mockResolvedValue(
    task("DRAFT", { assignee_id: "annotator-current" }),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("AnnotationQueuePage status architecture", () => {
  it.each([
    ["DRAFT", "待标注"],
    ["SUBMITTED", "待审核"],
    ["NEEDS_REVISION", "待修改"],
    ["APPROVED", "标注完成"],
  ] as const)(
    "maps backend %s to the %s queue without merging semantics",
    async (status, label) => {
      listTasksMock.mockResolvedValue([task(status)]);
      const query = status === "DRAFT" ? "" : `?stage=${status}`;
      renderQueue("annotation", `/annotations/annotate${query}`);

      const statusNavigation = await screen.findByRole("navigation", {
        name: "标注任务状态",
      });
      const selected = within(statusNavigation.parentElement!).getByRole(
        "link",
        { name: new RegExp(`^${label}，`) },
      );
      expect(selected).toHaveAttribute("aria-current", "page");
      const table = screen.getByRole("table", {
        name: `${label}任务列表`,
      });
      expect(within(table).getByText(label)).toBeVisible();
    },
  );

  it("does not expose rejected submissions as a task queue", async () => {
    listTasksMock.mockResolvedValue([task("REJECTED")]);
    renderQueue("annotation");

    await screen.findByRole("navigation", { name: "标注任务状态" });
    expect(
      screen.queryByRole("link", { name: /^已拒绝，/ }),
    ).not.toBeInTheDocument();
  });

  it("defaults /annotations/annotate to DRAFT and the historical tag-review route to SUBMITTED", async () => {
    renderQueue("annotation");
    expect(
      await screen.findByRole("link", {
        name: /^待标注，.*当前队列$/,
      }),
    ).toHaveAttribute("aria-current", "page");
    cleanup();

    renderQueue("tag-review");
    expect(
      await screen.findByRole("link", {
        name: /^待审核，.*当前队列$/,
      }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("round-trips only legal stage values through the query codec", () => {
    const approved = annotationQueueQueryCodec.parse("?stage=APPROVED&q=robot");
    expect(approved.stage).toBe("APPROVED");
    expect(annotationQueueQueryCodec.build(approved)).toContain(
      "stage=APPROVED",
    );
    expect(
      annotationQueueQueryCodec.parse("?stage=PUBLISH_PENDING").stage,
    ).toBe("DRAFT");
    expect(
      annotationQueueQueryCodec.parse("?stage=invalid", false, "SUBMITTED")
        .stage,
    ).toBe("SUBMITTED");
  });

  it("exposes stage deep links and keeps revisions as a separate record entry", async () => {
    renderQueue("annotation");

    expect(
      await screen.findByRole("link", { name: /^待审核，/ }),
    ).toHaveAttribute("href", "/annotations/annotate?stage=SUBMITTED");
    expect(screen.getByRole("link", { name: "修订记录" })).toHaveAttribute(
      "href",
      "/annotations/revisions",
    );
    expect(screen.queryByRole("link", { name: "Tag 审核" })).toBeNull();
    expect(screen.queryByRole("link", { name: "数据修订" })).toBeNull();
  });

  it("does not expose publishing as an annotation task state", async () => {
    listTasksMock.mockResolvedValue([
      task("APPROVED"),
      task("APPROVED", { task_id: "task-approved-2" }),
    ]);
    renderQueue("annotation", "/annotations/annotate?stage=APPROVED");

    expect(
      await screen.findByRole("link", { name: /^标注完成，.*2 项/u }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.queryByText(/待发布/u)).toBeNull();
    expect(screen.queryByText("流程边界")).toBeNull();
  });

  it.each(["annotator-current", "annotator-other"])(
    "shows view-only submission by %s without annotation.review",
    async (submittedBy) => {
      listTasksMock.mockResolvedValue([
        task("SUBMITTED", { submitted_by: submittedBy }),
      ]);
      renderQueue("tag-review");

      expect(
        await screen.findByRole("link", { name: "查看提交" }),
      ).toHaveAttribute("href", "/annotations/tag-review/task-submitted");
      expect(screen.queryByRole("link", { name: "开始审核" })).toBeNull();
    },
  );

  it.each(["annotator-current", "annotator-other"])(
    "allows reviewing submissions by %s with annotation.review",
    async (submittedBy) => {
      grantedCapabilities.add("annotation.review");
      listTasksMock.mockResolvedValue([
        task("SUBMITTED", { submitted_by: submittedBy }),
      ]);
      renderQueue("tag-review");

      expect(
        await screen.findByRole("link", { name: "开始审核" }),
      ).toHaveAttribute("href", "/annotations/tag-review/task-submitted");
    },
  );

  it.each([
    [
      "DRAFT",
      "/annotations/annotate",
      { assignee_id: null },
      ["annotation_task.claim"],
      "进入标注",
    ],
    [
      "DRAFT",
      "/annotations/annotate",
      { assignee_id: "annotator-current" },
      ["annotation.edit", "annotation_draft.edit"],
      "继续标注",
    ],
    [
      "NEEDS_REVISION",
      "/annotations/annotate?stage=NEEDS_REVISION",
      { assignee_id: "annotator-current" },
      ["annotation.edit", "annotation_draft.edit"],
      "修改标注",
    ],
    ["REJECTED", "/annotations/annotate?stage=REJECTED", {}, [], "查看驳回"],
    ["APPROVED", "/annotations/annotate?stage=APPROVED", {}, [], "查看结果"],
  ] as const)(
    "derives the %s operation from status, assignment and capabilities",
    async (status, path, overrides, capabilities, label) => {
      for (const capability of capabilities)
        grantedCapabilities.add(capability);
      listTasksMock.mockResolvedValue([task(status, overrides)]);
      renderQueue("annotation", path);

      expect(
        await screen.findByRole(label === "进入标注" ? "button" : "link", {
          name: label,
        }),
      ).toBeVisible();
    },
  );

  it("keeps an empty queue inside the task container with a recovery path", async () => {
    renderQueue("annotation");

    expect(await screen.findByText("待标注队列为空")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新检查队列" })).toBeVisible();
  });
});

describe("AnnotationQueuePage row entry", () => {
  it("claims an unassigned task once from a cell click, then opens it with the queue return location", async () => {
    const draft = task("DRAFT");
    const claimed = task("DRAFT", { assignee_id: "annotator-current" });
    grantedCapabilities.add("annotation_task.claim");
    listTasksMock.mockResolvedValue([draft]);
    let finishClaim!: (value: RuntimeAnnotationTask) => void;
    claimTaskMock.mockReturnValue(
      new Promise<RuntimeAnnotationTask>((resolve) => {
        finishClaim = resolve;
      }),
    );
    const queuePath = "/annotations/annotate?q=rollout-draft";
    renderQueue("annotation", queuePath);

    const row = (
      await screen.findByRole("button", { name: "进入标注" })
    ).closest("tr")!;
    const cell = within(row).getByText("900 步");
    fireEvent.click(cell);
    fireEvent.click(cell);

    expect(claimTaskMock).toHaveBeenCalledTimes(1);
    expect(claimTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-fe13",
        regionCode: "cn-shanghai",
      }),
      draft,
    );
    expect(row).toHaveAttribute("aria-busy", "true");
    expect(
      within(row).getByRole("button", { name: "正在进入…" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("当前位置").textContent).toBe(queuePath);

    listTasksMock.mockResolvedValue([claimed]);
    await act(async () => finishClaim(claimed));
    await waitFor(() =>
      expect(screen.getByLabelText("当前位置").textContent).toBe(
        `/annotations/tasks/task-draft?returnTo=${encodeURIComponent(queuePath)}`,
      ),
    );
    expect(listTasksMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the queue open when claiming fails and lets the entry button retry", async () => {
    grantedCapabilities.add("annotation_task.claim");
    listTasksMock.mockResolvedValue([task("DRAFT")]);
    claimTaskMock.mockRejectedValueOnce(
      new Error("任务暂时无法领取，请重试。"),
    );
    renderQueue("annotation");

    fireEvent.click(await screen.findByRole("button", { name: "进入标注" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "任务暂时无法领取，请重试。",
    );
    expect(screen.getByLabelText("当前位置").textContent).toBe(
      "/annotations/annotate",
    );
    expect(claimTaskMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "进入标注" }));
    await waitFor(() =>
      expect(screen.getByLabelText("当前位置").textContent).toContain(
        "/annotations/tasks/task-draft?",
      ),
    );
    expect(claimTaskMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(["Enter", " "])(
    "opens an assigned row with the %s key without claiming again",
    async (key) => {
      listTasksMock.mockResolvedValue([
        task("DRAFT", { assignee_id: "annotator-current" }),
      ]);
      renderQueue("annotation");

      const row = (
        await screen.findByRole("link", { name: "打开任务" })
      ).closest("tr")!;
      expect(row).toHaveAttribute("tabindex", "0");
      fireEvent.keyDown(row, { key });
      await waitFor(() =>
        expect(screen.getByLabelText("当前位置").textContent).toContain(
          "/annotations/tasks/task-draft?",
        ),
      );
      expect(claimTaskMock).not.toHaveBeenCalled();
    },
  );

  it("opens the submitted row in review and preserves the action link", async () => {
    listTasksMock.mockResolvedValue([task("SUBMITTED")]);
    renderQueue("tag-review");

    const link = await screen.findByRole("link", { name: "查看提交" });
    expect(link).toHaveAttribute(
      "href",
      "/annotations/tag-review/task-submitted",
    );
    fireEvent.click(within(link.closest("tr")!).getByText("900 步"));
    await waitFor(() =>
      expect(screen.getByLabelText("当前位置").textContent).toBe(
        "/annotations/tag-review/task-submitted",
      ),
    );
    expect(claimTaskMock).not.toHaveBeenCalled();
  });

  it("opens an unassigned task for viewing without claim permission", async () => {
    listTasksMock.mockResolvedValue([task("DRAFT")]);
    renderQueue("annotation");

    const row = (
      await screen.findByRole("button", { name: "查看任务" })
    ).closest("tr")!;
    fireEvent.click(within(row).getByText("900 步"));
    await waitFor(() =>
      expect(screen.getByLabelText("当前位置").textContent).toContain(
        "/annotations/tasks/task-draft?",
      ),
    );
    expect(claimTaskMock).not.toHaveBeenCalled();
  });

  it("does not claim or open a row without detail read permission", async () => {
    grantedCapabilities.add("annotation_task.claim");
    grantedCapabilities.delete("episode.read");
    listTasksMock.mockResolvedValue([task("DRAFT")]);
    renderQueue("annotation");

    const entry = await screen.findByRole("button", { name: "进入标注" });
    const row = entry.closest("tr")!;
    expect(entry).toBeDisabled();
    expect(row).not.toHaveAttribute("tabindex");
    fireEvent.click(within(row).getByText("900 步"));
    fireEvent.keyDown(row, { key: "Enter" });
    expect(screen.getByLabelText("当前位置").textContent).toBe(
      "/annotations/annotate",
    );
    expect(claimTaskMock).not.toHaveBeenCalled();
  });
});

describe("AnnotationQueuePage search and authorization semantics", () => {
  it("gives the task search a stable label, name, autocomplete and ellipsis", async () => {
    renderQueue("annotation");

    const search = await screen.findByLabelText(
      "搜索任务、Rollout、Dataset 或数据结构",
    );
    expect(search).toHaveAttribute("name", "annotation-task-search");
    expect(search).toHaveAttribute("autocomplete", "off");
    expect(search).toHaveAttribute(
      "placeholder",
      "搜索任务 / Rollout / Dataset…",
    );
  });

  it("keeps the queue unavailable without annotation_task.read", async () => {
    grantedCapabilities.clear();
    renderQueue("annotation");

    expect(
      await screen.findByText("你没有访问此标注资源的权限。"),
    ).toBeVisible();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});

describe("AnnotationQueuePage pagination", () => {
  function tasks(status: AnnotationQueueStage, count: number) {
    return Array.from({ length: count }, (_, index) =>
      task(status, {
        task_id: `${status}-task-${index + 1}`,
        rollout_id: `${status}-rollout-${index + 1}`,
        assignee_id: "annotator-current",
      }),
    );
  }

  it("pages through all tasks, keeps totals and return links, and omits the schema column", async () => {
    listTasksMock.mockResolvedValue(tasks("DRAFT", 45));
    renderQueue("annotation");

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(21);
    expect(screen.queryByRole("columnheader", { name: "标签结构" })).toBeNull();
    expect(screen.getByText("第 1–20 条，共 45 条")).toBeVisible();
    expect(
      screen.getByRole("link", { name: /^待标注，.*45 项/ }),
    ).toBeVisible();

    fireEvent.click(screen.getByTitle("2"));
    const task21 = await within(table).findByRole("row", {
      name: /DRAFT-rollout-21 /,
    });
    expect(within(table).queryByText("DRAFT-rollout-1")).toBeNull();
    expect(screen.getByText("第 21–40 条，共 45 条")).toBeVisible();
    expect(within(task21).getByRole("link")).toHaveAttribute(
      "href",
      "/annotations/tasks/DRAFT-task-21?returnTo=%2Fannotations%2Fannotate%3Fpage%3D2",
    );

    fireEvent.click(screen.getByTitle("3"));
    expect(await screen.findByText("第 41–45 条，共 45 条")).toBeVisible();
    expect(within(table).getAllByRole("row")).toHaveLength(6);
  });

  it("searches across pages and resets pagination when the search or stage changes", async () => {
    listTasksMock.mockResolvedValue([
      ...tasks("DRAFT", 45),
      ...tasks("APPROVED", 25),
    ]);
    renderQueue("annotation", "/annotations/annotate?page=2");

    await screen.findByText("第 21–40 条，共 45 条");
    const search = screen.getByRole("searchbox");
    fireEvent.change(search, { target: { value: "DRAFT-rollout-44" } });
    expect(await screen.findByText("第 1–1 条，共 1 条")).toBeVisible();
    expect(screen.getByText("DRAFT-rollout-44")).toBeVisible();
    fireEvent.change(search, { target: { value: "" } });
    expect(await screen.findByText("第 1–20 条，共 45 条")).toBeVisible();

    fireEvent.click(screen.getByTitle("2"));
    fireEvent.click(screen.getByRole("link", { name: /^标注完成，/ }));
    expect(await screen.findByText("第 1–20 条，共 25 条")).toBeVisible();
    expect(screen.getByText("APPROVED-rollout-1")).toBeVisible();
  });

  it("clamps stale page links to the last page and honors the requested page size", async () => {
    listTasksMock.mockResolvedValue(tasks("DRAFT", 55));
    renderQueue("annotation", "/annotations/annotate?page=999&limit=50");

    const table = await screen.findByRole("table");
    expect(screen.getByText("第 51–55 条，共 55 条")).toBeVisible();
    expect(within(table).getAllByRole("row")).toHaveLength(6);
    fireEvent.click(screen.getByTitle("1"));
    expect(await screen.findByText("第 1–50 条，共 55 条")).toBeVisible();
    expect(within(table).getAllByRole("row")).toHaveLength(51);
  });
});
