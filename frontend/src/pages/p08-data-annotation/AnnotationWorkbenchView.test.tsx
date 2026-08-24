// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JSX } from "react";
import { createDomainError } from "../../shared/api/domain-error";
import type { ViewerPanelRenderContext } from "../../features/viewer";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { RuntimeAnnotationTag } from "./runtime-annotation-adapter";
import type { TagReviewCheckResult } from "./tag-validation";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function panel(context: ViewerPanelRenderContext): JSX.Element {
  return (
    <article
      className="viewer-panel"
      aria-label={`${context.stream.displayName} test panel`}
    />
  );
}

function renderWorkbench(input: {
  readonly cameraCount: number;
  readonly mode?: "annotation" | "tag-review";
  readonly editable?: boolean;
  readonly dirty?: boolean;
  readonly externalError?: unknown;
  readonly autoAnnotationPanel?: JSX.Element;
  readonly onSelectTask?: (taskId: string) => void;
  readonly onPreSubmitCheck?: () => Promise<readonly TagReviewCheckResult[]>;
  readonly onRestoreRevision?: (targetRevision: number) => Promise<void>;
  readonly onSubmit?: () => Promise<void>;
}) {
  const mode = input.mode ?? "annotation";
  const bundle = createVisualAnnotationBundle({
    mode,
    cameraCount: input.cameraCount,
  });
  const tags =
    mode === "annotation"
      ? (bundle.draft?.tags ?? [])
      : (bundle.history.revisions.at(-1)?.tags ?? []);
  const onTagsChange = vi.fn<(tags: readonly RuntimeAnnotationTag[]) => void>();
  const onSave = vi.fn<() => Promise<void>>().mockResolvedValue();
  const onRestoreRevision =
    input.onRestoreRevision ??
    vi.fn<(targetRevision: number) => Promise<void>>().mockResolvedValue();
  const onOpenRevisions = vi.fn<() => void>();
  const onSubmit =
    input.onSubmit ?? vi.fn<() => Promise<void>>().mockResolvedValue();
  render(
    <AnnotationWorkbenchView
      {...(input.onPreSubmitCheck
        ? { onPreSubmitCheck: input.onPreSubmitCheck }
        : {})}
      autoAnnotationPanel={input.autoAnnotationPanel}
      bundle={bundle}
      dirty={input.dirty ?? false}
      externalError={input.externalError}
      mode={mode}
      permissions={{
        canEdit: input.editable ?? mode === "annotation",
        canSave: input.editable ?? mode === "annotation",
        canSubmit: input.editable ?? mode === "annotation",
        canReview: mode === "tag-review" && input.editable !== false,
        ...(input.editable === false
          ? { readOnlyReason: "测试：权限已撤销" }
          : {}),
      }}
      renderPanel={panel}
      scope={visualAnnotationScope}
      tags={tags}
      onReview={() => Promise.resolve()}
      onOpenRevisions={onOpenRevisions}
      onRestoreRevision={onRestoreRevision}
      onSave={onSave}
      onSelectTask={input.onSelectTask}
      onSubmit={onSubmit}
      onTagsChange={onTagsChange}
    />,
  );
  return {
    bundle,
    onOpenRevisions,
    onRestoreRevision,
    onSave,
    onSubmit,
    onTagsChange,
  };
}

const passedPreSubmitChecks: readonly TagReviewCheckResult[] = [
  {
    kind: "BOUNDARY",
    label: "标签区间边界准确",
    status: "PASS",
    evidence: "区间位于固定基线范围内",
  },
];

const failedPreSubmitChecks: readonly TagReviewCheckResult[] = [
  ...passedPreSubmitChecks,
  {
    kind: "OBJECT_RELATIONS",
    label: "动作与对象关系合理",
    status: "FAIL",
    evidence: "annotation-grasp-01 缺少 acts_on 对象关系",
  },
];

describe("P08 viewer-slot composition", () => {
  it.each([0, 1, 4, 8])(
    "keeps %i Manifest cameras on one shared timeline",
    (cameraCount) => {
      renderWorkbench({ cameraCount });
      expect(
        screen
          .getByLabelText("Manifest 相机视图")
          .querySelectorAll(".viewer-panel"),
      ).toHaveLength(cameraCount);
      expect(screen.getAllByRole("slider")).toHaveLength(1);
      expect(
        screen.getByText(`自动发现 ${cameraCount} 路相机 · 同一共享时间轴`),
      ).toBeInTheDocument();
    },
  );

  it("uses one main camera for review while retaining all Manifest camera choices", () => {
    renderWorkbench({ cameraCount: 8, mode: "tag-review" });
    expect(
      screen
        .getByLabelText("Manifest 相机视图")
        .querySelectorAll(".viewer-panel"),
    ).toHaveLength(1);
    expect(
      screen.getByLabelText(/当前相机/u).querySelectorAll("option"),
    ).toHaveLength(8);
    expect(screen.getAllByRole("slider")).toHaveLength(1);
    expect(screen.getByText("原始 / 修订差异")).toBeInTheDocument();
  });

  it("keeps a camera media binding stable across unrelated local UI changes", async () => {
    const user = userEvent.setup();
    const observedStreams: unknown[] = [];
    const stablePanel = (context: ViewerPanelRenderContext): JSX.Element => {
      observedStreams.push(context.stream);
      return <article aria-label="稳定媒体面板" />;
    };
    const bundle = createVisualAnnotationBundle({
      mode: "annotation",
      cameraCount: 1,
    });

    render(
      <AnnotationWorkbenchView
        bundle={bundle}
        dirty={false}
        mode="annotation"
        permissions={{
          canEdit: true,
          canSave: true,
          canSubmit: true,
          canReview: false,
        }}
        renderPanel={stablePanel}
        scope={visualAnnotationScope}
        tags={bundle.draft?.tags ?? []}
        onReview={() => Promise.resolve()}
        onSave={() => Promise.resolve()}
        onSubmit={() => Promise.resolve()}
        onTagsChange={() => undefined}
      />,
    );

    const initialStream = observedStreams.at(-1);
    await user.click(screen.getByRole("treeitem", { name: /抓取成功/u }));
    await user.click(screen.getByRole("button", { name: "数据信息" }));
    const dataInfoDrawer = screen.getByRole("dialog", { name: /数据信息/u });
    await user.click(
      within(dataInfoDrawer).getByRole("button", { name: /close/i }),
    );

    expect(observedStreams.at(-1)).toBe(initialStream);
  });

  it("keeps collection data folded by default and preserves task selection in the drawer", async () => {
    const user = userEvent.setup();
    const onSelectTask = vi.fn<(taskId: string) => void>();
    renderWorkbench({ cameraCount: 4, onSelectTask });

    expect(screen.queryByLabelText("采集条目导航")).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "数据信息" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("dialog", { name: /数据信息/u }),
    ).not.toBeInTheDocument();

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const drawer = screen.getByRole("dialog", { name: /数据信息/u });
    expect(
      within(drawer).getByRole("heading", { name: "采集条目" }),
    ).toBeVisible();
    expect(within(drawer).getAllByText("草稿").length).toBeGreaterThan(0);
    expect(within(drawer).getByText("任务 ID")).toBeVisible();
    expect(within(drawer).getByText("Schema")).toBeVisible();
    expect(within(drawer).getByText("Lance")).toBeVisible();
    expect(within(drawer).getByText("区间")).toBeVisible();
    expect(within(drawer).getByText("26,787 步")).toBeVisible();

    await user.click(
      within(drawer).getByRole("button", {
        name: /rollout-2026-08-18-0143/u,
      }),
    );
    expect(onSelectTask).toHaveBeenCalledWith("annotation-task-0143");

    const close = within(drawer).getByRole("button", { name: /close/i });
    close.focus();
    await user.click(close);
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: /数据信息/u }),
      ).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });
});

describe("P08 hierarchy, capabilities and conflicts", () => {
  it("keeps automatic annotation inside the annotation surface only", async () => {
    const user = userEvent.setup();
    renderWorkbench({
      cameraCount: 1,
      autoAnnotationPanel: <section aria-label="自动标注测试面板" />,
    });

    expect(screen.getByLabelText("自动标注测试面板")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "查看" }));
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "数据修订" }));
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "标注" }));
    expect(screen.getByLabelText("自动标注测试面板")).toBeVisible();
  });

  it("switches view, annotation, and immutable history inside one workbench", async () => {
    const { onOpenRevisions } = renderWorkbench({ cameraCount: 1 });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "查看" }));
    expect(screen.getByRole("heading", { name: "数据查看" })).toBeVisible();
    expect(
      screen.getByText(/媒体、数值流和时间轴继续使用同一工作台/u),
    ).toBeVisible();
    expect(screen.getAllByRole("slider")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "标注" }));
    expect(
      screen.getByRole("tree", { name: "多级 Tag Schema 层级" }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "数据修订" }));
    expect(
      screen.getByRole("heading", { name: "不可变数据修订" }),
    ).toBeVisible();
    expect(
      screen.getByRole("list", { name: "不可变数据修订历史" }),
    ).toBeVisible();
    expect(onOpenRevisions).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "打开全局修订列表" }));
    expect(onOpenRevisions).toHaveBeenCalledOnce();
  });

  it("exposes the full Tag tree, inherited attributes, interval and object relation", () => {
    renderWorkbench({ cameraCount: 4 });
    expect(
      screen.getByRole("tree", { name: "多级 Tag Schema 层级" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("操作阶段").length).toBeGreaterThan(0);
    expect(screen.getAllByText("抓取动作").length).toBeGreaterThan(0);
    expect(screen.getAllByText("抓取成功").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("开始步 *")).toHaveValue(301);
    expect(screen.getByLabelText("对象 ID *")).toHaveValue("workpiece-00125");
    expect(screen.getAllByText("[301, 451)").length).toBeGreaterThan(0);
  });

  it("becomes read-only when edit capabilities are revoked", () => {
    renderWorkbench({ cameraCount: 1, editable: false });
    expect(screen.getByText("测试：权限已撤销")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交审核" })).toBeDisabled();
    expect(screen.getByLabelText("开始步 *")).toBeDisabled();
  });

  it("shows a true 409 conflict and keeps writes disabled", () => {
    const error = createDomainError({
      code: "VERSION_CONFLICT",
      problemCode: "ANNOTATION_CONCURRENT_UPDATE",
      message: "If-Match 已过期",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: "request-409",
      retryable: false,
      httpStatus: 409,
    });
    renderWorkbench({ cameraCount: 4, externalError: error });
    expect(screen.getByText("并发版本冲突，写操作已暂停")).toBeInTheDocument();
    expect(screen.getByText(/request-409/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交审核" })).toBeDisabled();
    expect(screen.getByLabelText("开始步 *")).toBeDisabled();
  });

  it("requires a review comment for modification and rejection decisions", async () => {
    renderWorkbench({ cameraCount: 4, mode: "tag-review" });
    expect(screen.getByRole("button", { name: "要求修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
    await userEvent
      .setup()
      .type(screen.getByLabelText(/审核意见/u), "请修订对象关系");
    expect(screen.getByRole("button", { name: "要求修改" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeEnabled();
  });

  it("requires explicit confirmation before persisting an immutable modification", async () => {
    const user = userEvent.setup();
    const { onSave } = renderWorkbench({ cameraCount: 1, dirty: true });

    await user.click(screen.getByRole("button", { name: "保存修改" }));

    const dialog = screen.getByRole("dialog", { name: "保存标注修改" });
    expect(dialog).toHaveTextContent("新的不可变标注修订");
    expect(onSave).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认保存修改" }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("restores a selected historical data revision only after explicit confirmation", async () => {
    const user = userEvent.setup();
    const { onRestoreRevision } = renderWorkbench({ cameraCount: 1 });

    await user.click(screen.getByRole("button", { name: "数据修订" }));
    await user.selectOptions(screen.getByLabelText("回退目标修订"), "1");
    await user.click(screen.getByRole("button", { name: "回退为该修订" }));

    const dialog = screen.getByRole("dialog", { name: "回退标注数据修订" });
    expect(dialog).toHaveTextContent("新的不可变数据修订");
    expect(onRestoreRevision).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认回退为 r1" }));
    expect(onRestoreRevision).toHaveBeenCalledWith(1);
  });

  it("blocks navigation until an immutable restore and authoritative refetch finish", async () => {
    const user = userEvent.setup();
    let finishRestore: (() => void) | undefined;
    const pendingRestore = new Promise<void>((resolvePromise) => {
      finishRestore = resolvePromise;
    });
    const onRestoreRevision = vi.fn(() => pendingRestore);
    const { onOpenRevisions } = renderWorkbench({
      cameraCount: 1,
      onRestoreRevision,
    });

    await user.click(screen.getByRole("button", { name: "数据修订" }));
    await user.selectOptions(screen.getByLabelText("回退目标修订"), "1");
    await user.click(screen.getByRole("button", { name: "回退为该修订" }));
    await user.click(screen.getByRole("button", { name: "确认回退为 r1" }));

    const revisionHistory = screen.getByRole("button", { name: "数据修订" });
    expect(revisionHistory).toBeDisabled();
    await user.click(revisionHistory);
    expect(onOpenRevisions).not.toHaveBeenCalled();

    finishRestore?.();
    await waitFor(() => expect(revisionHistory).toBeEnabled());
    expect(
      screen.getByRole("heading", { name: "不可变数据修订" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "打开全局修订列表" }));
    expect(onOpenRevisions).toHaveBeenCalledOnce();
  });
});

describe("P08 submit precheck timing", () => {
  it("does not run or render the precheck during initialization, editing, or save", async () => {
    const user = userEvent.setup();
    const onPreSubmitCheck = vi
      .fn<() => Promise<readonly TagReviewCheckResult[]>>()
      .mockResolvedValue(passedPreSubmitChecks);
    renderWorkbench({ cameraCount: 1, dirty: true, onPreSubmitCheck });

    expect(
      screen.queryByRole("heading", { name: "提交前检查" }),
    ).not.toBeInTheDocument();
    expect(onPreSubmitCheck).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("对象 ID *"));
    expect(onPreSubmitCheck).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: "提交前检查" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存修改" }));
    await user.click(screen.getByRole("button", { name: "确认保存修改" }));
    expect(onPreSubmitCheck).not.toHaveBeenCalled();
  });

  it("starts once on submit click, shows loading, then continues to confirmation", async () => {
    const user = userEvent.setup();
    let finishCheck!: (checks: readonly TagReviewCheckResult[]) => void;
    const onPreSubmitCheck = vi.fn(
      () =>
        new Promise<readonly TagReviewCheckResult[]>((resolvePromise) => {
          finishCheck = resolvePromise;
        }),
    );
    let finishSubmit!: () => void;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolvePromise) => {
          finishSubmit = resolvePromise;
        }),
    );
    renderWorkbench({
      cameraCount: 1,
      onPreSubmitCheck,
      onSubmit,
    });

    const submit = screen.getByRole("button", { name: "提交审核" });
    await user.dblClick(submit);

    expect(onPreSubmitCheck).toHaveBeenCalledTimes(1);
    expect(screen.getByText("正在检查当前已保存草稿，请稍候…")).toBeVisible();
    expect(screen.getByRole("button", { name: "检查中…" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();

    finishCheck(passedPreSubmitChecks);
    const dialog = await screen.findByRole("dialog", {
      name: "提交 Tag 审核",
    });
    expect(dialog).toHaveTextContent("检查已通过");
    expect(onSubmit).not.toHaveBeenCalled();

    await user.dblClick(
      within(dialog).getByRole("button", { name: "确认提交审核" }),
    );
    expect(onSubmit).toHaveBeenCalledTimes(1);
    finishSubmit();
  });

  it("blocks submission and exposes failure reasons until the user edits", async () => {
    const user = userEvent.setup();
    const onPreSubmitCheck = vi
      .fn<() => Promise<readonly TagReviewCheckResult[]>>()
      .mockResolvedValue(failedPreSubmitChecks);
    const onSubmit = vi.fn<() => Promise<void>>().mockResolvedValue();
    renderWorkbench({ cameraCount: 1, onPreSubmitCheck, onSubmit });

    await user.click(screen.getByRole("button", { name: "提交审核" }));

    expect(
      await screen.findByRole("heading", { name: "提交前检查未通过" }),
    ).toBeVisible();
    expect(
      screen.getByText("annotation-grasp-01 缺少 acts_on 对象关系"),
    ).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("对象 ID *")).toBeEnabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("fails closed on a precheck exception and allows an explicit retry", async () => {
    const user = userEvent.setup();
    const onPreSubmitCheck = vi
      .fn<() => Promise<readonly TagReviewCheckResult[]>>()
      .mockRejectedValueOnce(new Error("gateway unavailable"))
      .mockResolvedValueOnce(passedPreSubmitChecks);
    const onSubmit = vi.fn<() => Promise<void>>().mockResolvedValue();
    renderWorkbench({ cameraCount: 1, onPreSubmitCheck, onSubmit });

    await user.click(screen.getByRole("button", { name: "提交审核" }));
    expect(await screen.findByText("提交前检查失败，请稍后重试")).toBeVisible();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "提交审核" }));
    expect(
      await screen.findByRole("dialog", { name: "提交 Tag 审核" }),
    ).toHaveTextContent("检查已通过");
    expect(onPreSubmitCheck).toHaveBeenCalledTimes(2);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
