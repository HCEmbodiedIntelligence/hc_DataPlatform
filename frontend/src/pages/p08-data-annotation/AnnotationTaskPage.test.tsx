// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import type { ComponentProps } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnnotationTaskPage, TagReviewTaskPage } from "./AnnotationTaskPage";
import type { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { RuntimeAnnotationBundle } from "../../features/annotation/runtime/runtime-annotation-adapter";
import { createDomainError } from "../../shared/api/domain-error";
import { annotationTaskListKey, taskQueryKey } from "../../features/annotation/runtime/task-preload";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

const { grantedCapabilities, loadBundleMock, reviewMock, reviewErrorMock, reopenMock, saveMock, refreshBundleMock, claimMock } = vi.hoisted(() => ({
  grantedCapabilities: new Set<string>(),
  loadBundleMock: vi.fn(),
  reviewMock: vi.fn(),
  reviewErrorMock: vi.fn(),
  reopenMock: vi.fn(),
  saveMock: vi.fn(),
  refreshBundleMock: vi.fn(),
  claimMock: vi.fn(),
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
    refreshRuntimeAnnotationBundle: refreshBundleMock,
    createRuntimeAnnotationCommands: () => ({ review: reviewMock, reopen: reopenMock, saveDraft: saveMock, claim: claimMock }),
  }),
);

vi.mock("./AnnotationWorkbenchView", () => ({
  AnnotationWorkbenchView: ({
    permissions,
    onReview,
    onReopenAnnotation,
    bundle,
    tags,
    dirty,
    onTagsChange,
    episodeOutcome,
    onEpisodeOutcomeChange,
    onSave,
    onCreateAnnotation,
    onSelectTask,
    onRefresh,
    navigatorPreferences,
    onNavigatorChange,
    switchingTask,
  }: ComponentProps<typeof AnnotationWorkbenchView>) => (
    <section data-testid="workbench">
      <button disabled={!permissions.canEdit} onClick={() => onEpisodeOutcomeChange?.("fail")}>标记失败</button>
      <output aria-label="Episode 结果">{episodeOutcome}</output>
      <button onClick={() => onTagsChange(tags.map((tag) => ({ ...tag, end_step: tag.end_step - 1 })))}>编辑区间</button>
      <button disabled={!permissions.canSave || !dirty} onClick={() => void onSave().catch(() => undefined)}>保存修改</button>
      <output aria-label="未保存">{String(dirty)}</output>
      <output aria-label="当前版本">{bundle.task.etag}</output>
      <output aria-label="当前状态">{bundle.task.status}</output>
      <output aria-label="当前数据">{bundle.task.task_id}</output>
      <output aria-label="切换中">{String(Boolean(switchingTask))}</output>
      <output aria-label="导航筛选">{navigatorPreferences?.stage}</output>
      <button onClick={() => onNavigatorChange?.({ ...navigatorPreferences!, stage: "pending", search: "任务 24", sameDataset: false })}>筛选待标注</button>
      <output aria-label="导航条件">{JSON.stringify(navigatorPreferences)}</output>
      <output aria-label="列表状态">{JSON.stringify(bundle.tasks.map(task => [task.task_id, task.status]))}</output>
      <button onClick={() => void onRefresh?.().catch(reviewErrorMock)}>刷新任务状态</button>
      <output aria-label="当前标注">{JSON.stringify(tags)}</output>
      {bundle.tasks.filter(task => task.task_id !== bundle.task.task_id).map(task => (
        <button key={task.task_id} onClick={() => onSelectTask?.(task.task_id)}>选择 {task.task_id}</button>
      ))}
      <button disabled={!permissions.canCreate} onClick={() => void onCreateAnnotation?.().catch(() => undefined)}>准备标注</button>
      {permissions.readOnlyReason ? <p>{permissions.readOnlyReason}</p> : null}
      <button disabled={!permissions.canReopen} onClick={() => void onReopenAnnotation?.()}>
        修改标注
      </button>
      <output aria-label="可编辑">{String(permissions.canEdit)}</output>
      {(["APPROVE", "NEEDS_REVISION", "REJECT"] as const).map((decision) => (
        <button
          key={decision}
          disabled={!permissions.canReview}
          onClick={() => void onReview(decision, "审核意见").catch(reviewErrorMock)}
        >
          {decision}
        </button>
      ))}
    </section>
  ),
}));

function RouteLocation() {
  return <output aria-label="当前路由">{useLocation().pathname}</output>;
}

function renderReview(bundle: RuntimeAnnotationBundle, mode: "annotation" | "tag-review" = "tag-review", cachedReview?: RuntimeAnnotationBundle, load?: () => Promise<RuntimeAnnotationBundle>) {
  if (load) loadBundleMock.mockImplementation(load);
  else loadBundleMock.mockResolvedValue(bundle);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (cachedReview) queryClient.setQueryData([
    "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
    visualAnnotationScope.projectId, visualAnnotationScope.regionCode, cachedReview.task.task_id, "tag-review",
  ], cachedReview);
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
        <RouteLocation />
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
  return queryClient;
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
  reviewMock.mockReset();
  reopenMock.mockReset();
  reopenMock.mockResolvedValue(undefined);
  saveMock.mockReset();
  saveMock.mockResolvedValue(undefined);
  refreshBundleMock.mockReset();
  refreshBundleMock.mockImplementation(async (_scope, bundle) => bundle);
  claimMock.mockReset();
  claimMock.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TagReviewTaskPage self review", () => {
  it("keeps the workbench and navigator preferences while switching to an uncached task in another mode", async () => {
    const first = createVisualAnnotationBundle({ mode: "annotation" });
    const review = createVisualAnnotationBundle({ mode: "tag-review" });
    const nextTask = { ...review.task, task_id: "uncached-review-task" };
    const tasks = [first.task, nextTask];
    const next = { ...review, task: nextTask, history: { ...review.history, task: nextTask,
      submissions: review.history.submissions.map(submission => ({ ...submission, task_id: nextTask.task_id })) }, tasks };
    let finish!: (bundle: RuntimeAnnotationBundle) => void;
    const loading = new Promise<RuntimeAnnotationBundle>(resolve => { finish = resolve; });
    renderReview({ ...first, tasks }, "annotation", undefined, () => loading);
    const workbench = screen.getByTestId("workbench");
    fireEvent.click(screen.getByRole("button", { name: "筛选待标注" }));
    const preferences = screen.getByLabelText("导航条件").textContent;
    fireEvent.click(screen.getByRole("button", { name: `选择 ${nextTask.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tag-review/${nextTask.task_id}`));
    expect(screen.getByTestId("workbench")).toBe(workbench);
    expect(screen.getByLabelText("当前数据")).toHaveTextContent(first.task.task_id);
    expect(screen.getByLabelText("切换中")).toHaveTextContent("true");
    expect(screen.getByLabelText("导航条件").textContent).toBe(preferences);
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    await act(async () => finish(next));
    await waitFor(() => expect(screen.getByLabelText("当前数据")).toHaveTextContent(nextTask.task_id));
    expect(screen.getByTestId("workbench")).toBe(workbench);
    expect(screen.getByLabelText("导航条件").textContent).toBe(preferences);
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeEnabled();
    expect(screen.getByLabelText("切换中")).toHaveTextContent("false");
  });

  it("carries the submitted version into review even if that route cached an earlier draft", async () => {
    const submitted = createVisualAnnotationBundle({ mode: "tag-review" });
    const cachedDraft = createVisualAnnotationBundle({ mode: "annotation" });
    const client = renderReview(submitted, "annotation", cachedDraft);
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tag-review/${submitted.task.task_id}`));
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeEnabled();
    expect(client.getQueryData([
      "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
      visualAnnotationScope.projectId, visualAnnotationScope.regionCode, submitted.task.task_id, "tag-review",
    ])).toMatchObject({ task: submitted.task, draft: null });
    expect(loadBundleMock.mock.calls.map(call => call[1])).not.toContain(submitted.task.task_id);
  });

  it.each([true, false])("routes a submitted annotation URL to review (review permission: %s)", async canReview => {
    if (!canReview) grantedCapabilities.delete("annotation.review");
    const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
    renderReview(bundle, "annotation");
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tag-review/${bundle.task.task_id}`));
    const review = await screen.findByRole("button", { name: "APPROVE" });
    if (canReview) expect(review).toBeEnabled();
    else {
      expect(review).toBeDisabled();
      expect(screen.getByText("当前授权仅允许查看，不允许创建审核决定。")).toBeVisible();
    }
    expect(screen.getByLabelText("可编辑")).toHaveTextContent("false");
    expect(reviewMock).not.toHaveBeenCalled();
    expect(claimMock).not.toHaveBeenCalled();
  });

  it("opens a submitted sidebar task in review when starting from annotation", async () => {
    const annotation = createVisualAnnotationBundle({ mode: "annotation" });
    const submitted = createVisualAnnotationBundle({ mode: "tag-review" });
    const firstTask = { ...annotation.task, task_id: "previous-annotation-task" };
    const tasks = [firstTask, submitted.task];
    const client = renderReview({ ...annotation, task: firstTask, history: { ...annotation.history, task: firstTask }, tasks }, "annotation");
    client.setQueryData([
      "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
      visualAnnotationScope.projectId, visualAnnotationScope.regionCode, submitted.task.task_id, "tag-review",
    ], { ...submitted, tasks });
    fireEvent.click(screen.getByRole("button", { name: `选择 ${submitted.task.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tag-review/${submitted.task.task_id}`));
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeEnabled();
    expect(reviewMock).not.toHaveBeenCalled();
  });

  it.each(["APPROVE", "NEEDS_REVISION", "REJECT"] as const)(
    "allows the submitter to submit %s with review permission",
    async (decision) => {
      const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
      renderReview(bundle);
      const completed = reviewedBundle(bundle, decision);
      reviewMock.mockResolvedValue(completed.task);
      refreshBundleMock.mockResolvedValue(completed);
      loadBundleMock.mockResolvedValue(completed);

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

function reviewedBundle(bundle: RuntimeAnnotationBundle, decision: "APPROVE" | "NEEDS_REVISION" | "REJECT" = "APPROVE"): RuntimeAnnotationBundle {
  const task = { ...bundle.task, state_version: bundle.task.state_version + 1,
    etag: `"reviewed-${decision}"`, status: decision === "APPROVE" ? "APPROVED" as const : decision === "REJECT" ? "REJECTED" as const : decision,
    approved_revision: decision === "APPROVE" ? bundle.task.current_revision : null,
    approved_review_id: decision === "APPROVE" ? "latest-review" : null };
  return { ...bundle, task, draft: null, history: { ...bundle.history, task },
    tasks: bundle.tasks.map(candidate => candidate.task_id === task.task_id ? task : candidate) };
}

describe("review status synchronization", () => {
  const revision = "session:1:capabilities:1";
  const key = (id: string, mode: "annotation" | "tag-review" = "tag-review") =>
    taskQueryKey(visualAnnotationScope, id, mode, revision);
  const listKey = annotationTaskListKey(visualAnnotationScope, revision);

  function setup() {
    const fixture = createVisualAnnotationBundle({ mode: "tag-review" });
    const otherTask = { ...reviewedBundle(fixture).task, task_id: "other-completed-task" };
    const tasks = [fixture.task, otherTask];
    const bundle = { ...fixture, tasks };
    const other = { ...bundle, task: otherTask, history: { ...bundle.history, task: otherTask } };
    const client = renderReview(bundle);
    client.setQueryData(key(bundle.task.task_id, "annotation"), bundle);
    client.setQueryData(key(otherTask.task_id), other);
    client.setQueryData(listKey, tasks);
    loadBundleMock.mockImplementation(async (_scope, id) => id === otherTask.task_id ? other : bundle);
    return { bundle, other, client };
  }

  it("keeps completed status in both modes, the queue and neighboring pages when switching away and back", async () => {
    const { bundle, other, client } = setup();
    const completed = reviewedBundle(bundle);
    reviewMock.mockResolvedValue(completed.task);
    refreshBundleMock.mockResolvedValue(completed);
    fireEvent.click(screen.getByRole("button", { name: "APPROVE" }));
    await waitFor(() => expect(screen.getByLabelText("当前状态")).toHaveTextContent("APPROVED"));
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    for (const mode of ["annotation", "tag-review"] as const)
      expect(client.getQueryData<RuntimeAnnotationBundle>(key(bundle.task.task_id, mode))?.task).toEqual(completed.task);
    expect(client.getQueryData<RuntimeAnnotationBundle["tasks"]>(listKey)?.[0]).toEqual(completed.task);
    expect(client.getQueryData<RuntimeAnnotationBundle>(key(other.task.task_id))?.tasks[0]).toEqual(completed.task);
    fireEvent.click(screen.getByRole("button", { name: `选择 ${other.task.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(other.task.task_id));
    expect(screen.getByLabelText("列表状态")).toHaveTextContent(JSON.stringify([bundle.task.task_id, "APPROVED"]));
    // A background read of the next task may still carry an older task list.
    await client.refetchQueries({ queryKey: key(other.task.task_id), exact: true });
    await waitFor(() => expect(screen.getByLabelText("列表状态")).toHaveTextContent(JSON.stringify([bundle.task.task_id, "APPROVED"])));
    fireEvent.click(screen.getByRole("button", { name: `选择 ${bundle.task.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(bundle.task.task_id));
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    expect(reviewMock).toHaveBeenCalledOnce();
  });

  it("disables the reviewed submission even when refreshing history fails", async () => {
    const { bundle, client } = setup();
    const completed = reviewedBundle(bundle);
    reviewMock.mockResolvedValue(completed.task);
    refreshBundleMock.mockRejectedValue(new Error("读取审核结果失败"));
    fireEvent.click(screen.getByRole("button", { name: "APPROVE" }));
    await waitFor(() => expect(reviewErrorMock).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("当前状态")).toHaveTextContent("APPROVED");
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    expect(client.getQueryData<RuntimeAnnotationBundle>(key(bundle.task.task_id, "annotation"))?.task).toEqual(completed.task);
  });

  it.each(["ANNOTATION_INVALID_STATE", "ANNOTATION_CONCURRENT_UPDATE"])("refreshes all task views after %s without repeating a write", async problemCode => {
    const { bundle, client } = setup();
    const error = createDomainError({ code: "VERSION_CONFLICT", problemCode,
      message: "only a submitted revision may be reviewed", fieldErrors: [], operationErrors: [], blockedReasons: [],
      requestId: "review-state-request", retryable: false, httpStatus: 409 });
    reviewMock.mockRejectedValue(error);
    const completed = reviewedBundle(bundle);
    refreshBundleMock.mockResolvedValue(completed);
    fireEvent.click(screen.getByRole("button", { name: "APPROVE" }));
    await waitFor(() => expect(reviewErrorMock).toHaveBeenCalledWith(error));
    expect(screen.getByLabelText("当前状态")).toHaveTextContent("APPROVED");
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeDisabled();
    expect(client.getQueryData<RuntimeAnnotationBundle>(key(bundle.task.task_id, "annotation"))?.task).toEqual(completed.task);
    expect(reviewMock).toHaveBeenCalledOnce();
  });

  it("preserves the original conflict if automatic refresh fails and lets the user refresh again", async () => {
    const { bundle } = setup();
    const error = createDomainError({ code: "VERSION_CONFLICT", problemCode: "ANNOTATION_INVALID_STATE",
      message: "only a submitted revision may be reviewed", fieldErrors: [], operationErrors: [], blockedReasons: [],
      requestId: "review-state-request", retryable: false, httpStatus: 409 });
    reviewMock.mockRejectedValue(error);
    refreshBundleMock.mockRejectedValueOnce(new Error("离线"));
    fireEvent.click(screen.getByRole("button", { name: "APPROVE" }));
    await waitFor(() => expect(reviewErrorMock).toHaveBeenCalledWith(error));
    refreshBundleMock.mockResolvedValue(reviewedBundle(bundle));
    fireEvent.click(screen.getByRole("button", { name: "刷新任务状态" }));
    await waitFor(() => expect(screen.getByLabelText("当前状态")).toHaveTextContent("APPROVED"));
    expect(reviewMock).toHaveBeenCalledOnce();
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
    await waitFor(() => expect(screen.getByLabelText("可编辑")).toHaveTextContent("true"));
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

describe("saving annotation changes in place", () => {
  function setup() {
    for (const capability of ["annotation.edit", "annotation_draft.edit", "annotation.save"])
      grantedCapabilities.add(capability);
    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    const bundle = { ...fixture, tasks: [fixture.task] };
    const client = renderReview(bundle, "annotation");
    return { bundle, client };
  }

  it("saves an episode result without adding a Tag and reloads the saved value", async () => {
    const { bundle } = setup();
    const task = { ...bundle.task, current_revision: bundle.task.current_revision + 1, etag: '"outcome-saved"' };
    const draft = { ...bundle.draft!, revision: task.current_revision, etag: task.etag, episode_outcome: "fail" as const };
    refreshBundleMock.mockResolvedValue({ ...bundle, task, draft, tasks: [task], history: { ...bundle.history, task } });
    fireEvent.click(screen.getByRole("button", { name: "标记失败" }));
    expect(screen.getByLabelText("未保存")).toHaveTextContent("true");
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(screen.getByLabelText("未保存")).toHaveTextContent("false"));
    expect(saveMock).toHaveBeenCalledWith(bundle.task, bundle.draft, bundle.draft!.tags, "fail");
    expect(screen.getByLabelText("Episode 结果")).toHaveTextContent("fail");
  });

  it("keeps the workbench mounted and uses the server's new version for consecutive saves", async () => {
    const { bundle, client } = setup();
    const workbench = screen.getByTestId("workbench");
    const task = { ...bundle.task, current_revision: bundle.task.current_revision + 1, etag: '"saved-version"' };
    const tags = bundle.draft!.tags.map((tag) => ({ ...tag, end_step: tag.end_step - 1 }));
    const draft = { ...bundle.draft!, revision: task.current_revision, etag: task.etag, tags };
    const saved = { ...bundle, task, draft, history: { ...bundle.history, task }, tasks: [task] };
    refreshBundleMock.mockResolvedValue(saved);

    fireEvent.click(screen.getByRole("button", { name: "编辑区间" }));
    expect(screen.getByLabelText("未保存")).toHaveTextContent("true");
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(screen.getByLabelText("当前版本")).toHaveTextContent(task.etag));
    expect(screen.getByLabelText("未保存")).toHaveTextContent("false");
    expect(screen.getByTestId("workbench")).toBe(workbench);
    expect(loadBundleMock).not.toHaveBeenCalled();
    expect(refreshBundleMock).toHaveBeenCalledWith(visualAnnotationScope, bundle);
    expect(saveMock).toHaveBeenCalledWith(bundle.task, bundle.draft, tags, "success");
    const cached = client.getQueryData<RuntimeAnnotationBundle>([
      "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
      visualAnnotationScope.projectId, visualAnnotationScope.regionCode, task.task_id, "annotation",
    ])!;
    expect(cached.manifest).toBe(bundle.manifest);
    expect(cached.datasetVersion).toBe(bundle.datasetVersion);

    fireEvent.click(screen.getByRole("button", { name: "编辑区间" }));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(2));
    expect(saveMock.mock.calls[1]![0]).toEqual(task);
    expect(saveMock.mock.calls[1]![1]).toEqual(draft);
  });

  it.each(["save", "refresh"])("preserves edits and the mounted page when %s fails", async (phase) => {
    const { bundle } = setup();
    const workbench = screen.getByTestId("workbench");
    if (phase === "save") saveMock.mockRejectedValue(new Error("保存失败"));
    else refreshBundleMock.mockRejectedValue(new Error("读取保存结果失败"));
    fireEvent.click(screen.getByRole("button", { name: "编辑区间" }));
    const editedTags = screen.getByLabelText("当前标注").textContent;
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledOnce());
    if (phase === "refresh") await waitFor(() => expect(refreshBundleMock).toHaveBeenCalledOnce());
    expect(screen.getByTestId("workbench")).toBe(workbench);
    expect(screen.getByLabelText("未保存")).toHaveTextContent("true");
    expect(screen.getByLabelText("当前标注").textContent).toBe(editedTags);
    expect(screen.getByLabelText("当前版本")).toHaveTextContent(bundle.task.etag);
    expect(loadBundleMock).not.toHaveBeenCalled();
  });
});

describe("preparing an annotation task", () => {
  function unassignedBundle() {
    for (const capability of ["annotation_task.claim", "annotation.edit", "annotation_draft.edit", "annotation.save"])
      grantedCapabilities.add(capability);
    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    const task = { ...fixture.task, assignee_id: null };
    return { ...fixture, task, draft: null, history: { ...fixture.history, task }, tasks: [task] };
  }

  it("makes an unassigned task editable while preserving the loaded playback bundle", async () => {
    const bundle = unassignedBundle();
    const ready = createVisualAnnotationBundle({ mode: "annotation" });
    const claimed = { ...bundle, task: ready.task, draft: ready.draft, history: ready.history, tasks: [ready.task] };
    refreshBundleMock.mockResolvedValue(claimed);
    renderReview(bundle, "annotation");
    const workbench = screen.getByTestId("workbench");
    expect(screen.getByLabelText("可编辑")).toHaveTextContent("false");
    fireEvent.click(screen.getByRole("button", { name: "准备标注" }));
    await waitFor(() => expect(screen.getByLabelText("可编辑")).toHaveTextContent("true"));
    expect(claimMock).toHaveBeenCalledExactlyOnceWith(bundle.task);
    expect(refreshBundleMock).toHaveBeenCalledWith(visualAnnotationScope, bundle);
    expect(loadBundleMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("workbench")).toBe(workbench);
  });

  it.each(["annotation_task.claim", "annotation.edit", "annotation_draft.edit", "another-assignee", "submitted"])(
    "does not prepare a task with %s", async (restriction) => {
      const bundle = unassignedBundle();
      if (restriction === "another-assignee") Object.assign(bundle.task, { assignee_id: "another-annotator" });
      else if (restriction === "submitted") Object.assign(bundle.task, { status: "SUBMITTED" });
      else grantedCapabilities.delete(restriction);
      renderReview(bundle, "annotation");
      expect(await screen.findByRole("button", { name: "准备标注" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "准备标注" }));
      expect(claimMock).not.toHaveBeenCalled();
    },
  );

  it.each(["DRAFT", "NEEDS_REVISION"] as const)("redirects an existing review URL for a %s task into annotation", async status => {
    const bundle = unassignedBundle();
    Object.assign(bundle.task, { status });
    renderReview(bundle, "tag-review");
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tasks/${bundle.task.task_id}`));
    await screen.findByRole("button", { name: "准备标注" });
    if (status === "DRAFT") expect(screen.getByRole("button", { name: "准备标注" })).toBeEnabled();
  });

  it("opens a pending sidebar task in the annotation route when starting from review", async () => {
    const pending = unassignedBundle();
    const review = createVisualAnnotationBundle({ mode: "tag-review" });
    const nextTask = { ...pending.task, task_id: "next-pending-task" };
    const next = { ...pending, task: nextTask, history: { ...pending.history, task: nextTask }, tasks: [review.task, nextTask] };
    const client = renderReview({ ...review, tasks: [review.task, nextTask] }, "tag-review");
    client.setQueryData([
      "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
      visualAnnotationScope.projectId, visualAnnotationScope.regionCode, nextTask.task_id, "annotation",
    ], next);
    fireEvent.click(screen.getByRole("button", { name: `选择 ${nextTask.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tasks/${nextTask.task_id}`));
    expect(screen.getByRole("button", { name: "准备标注" })).toBeEnabled();
  });

  it("opens a submitted sidebar task directly in review when starting from annotation", async () => {
    const pending = unassignedBundle();
    const review = createVisualAnnotationBundle({ mode: "tag-review" });
    const nextTask = { ...review.task, task_id: "next-submitted-task" };
    const next = { ...review, task: nextTask, history: { ...review.history, task: nextTask,
      submissions: review.history.submissions.map(submission => ({ ...submission, task_id: nextTask.task_id })),
    }, tasks: [pending.task, nextTask] };
    const client = renderReview({ ...pending, tasks: [pending.task, nextTask] }, "annotation");
    client.setQueryData([
      "p08-runtime-annotation", visualAnnotationScope.organizationId, "session:1:capabilities:1",
      visualAnnotationScope.projectId, visualAnnotationScope.regionCode, nextTask.task_id, "tag-review",
    ], next);
    fireEvent.click(screen.getByRole("button", { name: `选择 ${nextTask.task_id}` }));
    await waitFor(() => expect(screen.getByLabelText("当前路由")).toHaveTextContent(`/annotations/tag-review/${nextTask.task_id}`));
    expect(screen.getByRole("button", { name: "APPROVE" })).toBeEnabled();
  });
});
