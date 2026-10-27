// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import type { ComponentProps } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnnotationTaskPage, TagReviewTaskPage } from "./AnnotationTaskPage";
import type { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { RuntimeAnnotationBundle } from "../../features/annotation/runtime/runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

const { grantedCapabilities, loadBundleMock, reviewMock, reopenMock } = vi.hoisted(() => ({
  grantedCapabilities: new Set<string>(),
  loadBundleMock: vi.fn(),
  reviewMock: vi.fn(),
  reopenMock: vi.fn(),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => grantedCapabilities.has(capability),
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../shared/scope/shell-store", () => ({
  useShellStore: Object.assign((selector: (state: object) => unknown) =>
    selector({
      principal: { actorId: "visual-annotator" },
      scope: visualAnnotationScope,
      sessionRevision: 1,
      capabilityRevision: 1,
    }), { subscribe: () => () => {} }),
}));

vi.mock("../../features/robots/api/hooks", () => ({
  useScopedRobotBootstrap: () => ({ data: undefined }),
}));

vi.mock("../../features/robot-models/api/hooks", () => ({
  useScopedRobotModelVersion: () => ({ data: undefined }),
  useScopedRobotModelAssets: () => ({ data: undefined }),
  useScopedRobotModelJointMappings: () => ({ data: undefined }),
}));

vi.mock(
  "../../features/annotation/runtime/runtime-annotation-adapter",
  async (importOriginal) => ({
    ...(await importOriginal<
      typeof import("../../features/annotation/runtime/runtime-annotation-adapter")
    >()),
    loadRuntimeAnnotationBundle: loadBundleMock,
    createRuntimeAnnotationCommands: () => ({ review: reviewMock, reopen: reopenMock }),
  }),
);

vi.mock("./AnnotationWorkbenchView", () => ({
  AnnotationWorkbenchView: ({
    permissions,
    onReview,
    onReopenAnnotation,
  }: ComponentProps<typeof AnnotationWorkbenchView>) => (
    <section>
      {permissions.readOnlyReason ? <p>{permissions.readOnlyReason}</p> : null}
      <button disabled={!permissions.canReopen} onClick={() => void onReopenAnnotation?.()}>
        修改标注
      </button>
      <output aria-label="可编辑">{String(permissions.canEdit)}</output>
      {(["APPROVE", "NEEDS_REVISION", "REJECT"] as const).map((decision) => (
        <button
          key={decision}
          disabled={!permissions.canReview}
          onClick={() => void onReview(decision, "审核意见")}
        >
          {decision}
        </button>
      ))}
    </section>
  ),
}));

function renderReview(bundle: RuntimeAnnotationBundle, mode: "annotation" | "tag-review" = "tag-review") {
  loadBundleMock.mockResolvedValue(bundle);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(
    [
      "p08-runtime-annotation",
      visualAnnotationScope.organizationId,
      "session:1:capabilities:1",
      visualAnnotationScope.projectId,
      visualAnnotationScope.regionCode,
      bundle.task.task_id,
      mode,
    ],
    bundle,
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[mode === "tag-review" ? `/annotations/tag-review/${bundle.task.task_id}` : `/annotations/tasks/${bundle.task.task_id}`]}
      >
        <Routes>
          <Route path="/annotations/tasks/:taskId" element={<AnnotationTaskPage />} />
          <Route
            path="/annotations/tag-review/:taskId"
            element={<TagReviewTaskPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  grantedCapabilities.clear();
  for (const capability of [
    "annotation_task.read",
    "episode.read",
    "dataset_version.read",
    "annotation.review",
  ])
    grantedCapabilities.add(capability);
  reviewMock.mockResolvedValue(undefined);
  reopenMock.mockReset();
  reopenMock.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TagReviewTaskPage self review", () => {
  it.each(["APPROVE", "NEEDS_REVISION", "REJECT"] as const)(
    "allows the submitter to submit %s with review permission",
    async (decision) => {
      const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
      renderReview(bundle);

      const button = screen.getByRole("button", { name: decision });
      expect(button).toBeEnabled();
      expect(screen.queryByText(/当前账号不能自审/u)).toBeNull();
      fireEvent.click(button);

      await waitFor(() =>
        expect(reviewMock).toHaveBeenCalledWith(
          bundle.task,
          expect.objectContaining({
            submission_id: bundle.task.current_submission_id,
          }),
          decision,
          "审核意见",
        ),
      );
    },
  );

  it("keeps self review disabled without review permission", () => {
    grantedCapabilities.delete("annotation.review");
    renderReview(createVisualAnnotationBundle({ mode: "tag-review" }));

    expect(
      screen.getByText("当前授权仅允许查看，不允许创建审核决定。"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "APPROVE" }));
    expect(reviewMock).not.toHaveBeenCalled();
  });

  it("keeps self review disabled without the submitted snapshot", () => {
    const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
    renderReview({
      ...bundle,
      history: { ...bundle.history, submissions: [] },
    });

    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    expect(reviewMock).not.toHaveBeenCalled();
  });
});

describe("completed annotation editing", () => {
  function completedBundle(mode: "annotation" | "tag-review") {
    const bundle = createVisualAnnotationBundle({ mode });
    return { ...bundle, task: { ...bundle.task, status: "APPROVED" as const,
      approved_revision: bundle.task.current_revision, approved_review_id: "approved-review" } };
  }
  function allowEditing() {
    for (const capability of ["annotation.edit", "annotation_draft.edit", "annotation.save"])
      grantedCapabilities.add(capability);
  }

  it.each(["annotation", "tag-review"] as const)("reopens from the %s result page", async mode => {
    allowEditing();
    const completed = completedBundle(mode);
    renderReview(completed, mode);
    expect(screen.getByRole("button", { name: "修改标注" })).toBeEnabled();
    expect(screen.getByLabelText("可编辑")).toHaveTextContent("false");
    const draft = createVisualAnnotationBundle({ mode: "annotation" });
    reopenMock.mockImplementation(async () => { loadBundleMock.mockResolvedValue(draft); });
    fireEvent.click(screen.getByRole("button", { name: "修改标注" }));
    await waitFor(() => expect(reopenMock).toHaveBeenCalledWith(completed.task));
    await waitFor(() => expect(screen.getByRole("button", { name: "修改标注" })).toBeDisabled());
    expect(screen.getByLabelText("可编辑")).toHaveTextContent("true");
  });

  it.each(["annotation.edit", "annotation_draft.edit", "annotation.save", "assignee"])(
    "keeps completed editing unavailable without %s", missing => {
      allowEditing();
      const completed = completedBundle("annotation");
      if (missing === "assignee") completed.task.assignee_id = "another-annotator";
      else grantedCapabilities.delete(missing);
      renderReview(completed, "annotation");
      expect(screen.getByRole("button", { name: "修改标注" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "修改标注" }));
      expect(reopenMock).not.toHaveBeenCalled();
    },
  );
});
