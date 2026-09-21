// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AnnotationQueuePage, TagReviewQueuePage } from "./AnnotationQueuePage";
import { annotationQueueQueryCodec } from "./query-codec";
import type { AnnotationQueueStage } from "./query-codec";
import type { RuntimeAnnotationTask } from "./runtime-annotation-adapter";

const { grantedCapabilities, listTasksMock } = vi.hoisted(() => ({
  grantedCapabilities: new Set<string>(),
  listTasksMock: vi.fn(),
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

vi.mock("./runtime-annotation-adapter", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("./runtime-annotation-adapter")>();
  return {
    ...actual,
    listRuntimeAnnotationTasks: listTasksMock,
  };
});

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
      "领取标注",
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
        await screen.findByRole(/领取/u.test(label) ? "button" : "link", {
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

describe("AnnotationQueuePage search and authorization semantics", () => {
  it("gives the task search a stable label, name, autocomplete and ellipsis", async () => {
    renderQueue("annotation");

    const search = await screen.findByLabelText(
      "搜索任务、Rollout、Dataset 或数据结构",
    );
    expect(search).toHaveAttribute("name", "annotation-task-search");
    expect(search).toHaveAttribute("autocomplete", "off");
    expect(search).toHaveAttribute("placeholder", "输入关键词…");
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
