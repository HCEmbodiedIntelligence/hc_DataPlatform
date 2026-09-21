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
import { TagReviewTaskPage } from "./AnnotationTaskPage";
import type { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { RuntimeAnnotationBundle } from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

const { grantedCapabilities, loadBundleMock, reviewMock } = vi.hoisted(() => ({
  grantedCapabilities: new Set<string>(),
  loadBundleMock: vi.fn(),
  reviewMock: vi.fn(),
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
      principal: { actorId: "visual-annotator" },
      scope: visualAnnotationScope,
    }),
}));

vi.mock("../../features/robots/api", () => ({
  useRobotBootstrap: () => ({ data: undefined }),
}));

vi.mock("../../features/robot-models/api", () => ({
  useRobotModelVersion: () => ({ data: undefined }),
  useRobotModelAssets: () => ({ data: undefined }),
  useRobotModelJointMappings: () => ({ data: undefined }),
}));

vi.mock("./runtime-annotation-adapter", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./runtime-annotation-adapter")>()),
  loadRuntimeAnnotationBundle: loadBundleMock,
  createRuntimeAnnotationCommands: () => ({ review: reviewMock }),
}));

vi.mock("./AnnotationWorkbenchView", () => ({
  AnnotationWorkbenchView: ({
    permissions,
    onReview,
  }: ComponentProps<typeof AnnotationWorkbenchView>) => (
    <section>
      {permissions.readOnlyReason ? <p>{permissions.readOnlyReason}</p> : null}
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

function renderReview(bundle: RuntimeAnnotationBundle) {
  loadBundleMock.mockResolvedValue(bundle);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(
    [
      "p08-runtime-annotation",
      visualAnnotationScope.projectId,
      visualAnnotationScope.regionCode,
      bundle.task.task_id,
      "tag-review",
    ],
    bundle,
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[`/annotations/tag-review/${bundle.task.task_id}`]}
      >
        <Routes>
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
    "annotation.review",
  ])
    grantedCapabilities.add(capability);
  reviewMock.mockResolvedValue(undefined);
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
