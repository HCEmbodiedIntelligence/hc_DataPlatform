// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AnnotationQueuePage, TagReviewQueuePage } from "./AnnotationQueuePage";

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

function renderQueue(mode: "annotation" | "tag-review") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Component =
    mode === "annotation" ? AnnotationQueuePage : TagReviewQueuePage;
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[
          mode === "annotation"
            ? "/annotations/annotate"
            : "/annotations/tag-review",
        ]}
      >
        <Component />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  grantedCapabilities.clear();
  grantedCapabilities.add("annotation_task.read");
  listTasksMock.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnnotationQueuePage navigation and search semantics", () => {
  it.each([
    ["annotation", "数据标注", "Tag 审核"],
    ["tag-review", "Tag 审核", "数据标注"],
  ] as const)(
    "uses route-backed links and aria-current in %s mode",
    async (mode, currentLabel, otherLabel) => {
      renderQueue(mode);

      const current = await screen.findByRole("link", { name: currentLabel });
      const other = screen.getByRole("link", { name: otherLabel });
      expect(current).toBeInstanceOf(HTMLAnchorElement);
      expect(other).toBeInstanceOf(HTMLAnchorElement);
      expect(current).toHaveAttribute("aria-current", "page");
      expect(other).not.toHaveAttribute("aria-current");
      expect(screen.getByRole("link", { name: "数据标注" })).toHaveAttribute(
        "href",
        "/annotations/annotate",
      );
      expect(screen.getByRole("link", { name: "Tag 审核" })).toHaveAttribute(
        "href",
        "/annotations/tag-review",
      );
      expect(screen.queryByRole("button", { name: currentLabel })).toBeNull();
    },
  );

  it("gives the task search a stable name, disabled autocomplete and Chinese ellipsis", async () => {
    renderQueue("annotation");

    const search = await screen.findByLabelText(
      "搜索任务、Rollout、Dataset 或 Schema",
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
