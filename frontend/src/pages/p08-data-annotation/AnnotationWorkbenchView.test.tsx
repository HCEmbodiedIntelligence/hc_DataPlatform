// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { useState } from "react";
import type { JSX } from "react";
import { createDomainError } from "../../shared/api/domain-error";
import type { ViewerPanelRenderContext } from "../../features/viewer";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { AnnotationWorkbenchPermissions } from "./AnnotationWorkbenchView";
import type { DataIssueReportInput } from "./AnnotationWorkbenchView";
import type {
  RuntimeAnnotationBundle,
  RuntimeAnnotationTag,
  RuntimeReviewDecision,
} from "./runtime-annotation-adapter";
import type { TagReviewCheckResult } from "./tag-validation";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
    configurable: true,
    value: () => undefined,
  });
  if (!window.PointerEvent) {
    class TestPointerEvent extends MouseEvent {
      readonly pointerId: number;

      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    }
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      value: TestPointerEvent,
    });
  }
});

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
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
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

function workspaceTab(label: "查看" | "标注" | "Tag 审核" | "数据修订") {
  const tab = screen
    .getAllByRole("tab")
    .find(
      (candidate) => candidate.querySelector("strong")?.textContent === label,
    );
  if (!tab) throw new Error(`Workspace tab ${label} was not rendered`);
  return tab;
}

function bundleWithoutDraft(
  bundle: RuntimeAnnotationBundle,
): RuntimeAnnotationBundle {
  return {
    ...bundle,
    task: {
      ...bundle.task,
      assignee_id: null,
      current_revision: 0,
      status: "DRAFT",
    },
    draft: null,
    history: {
      ...bundle.history,
      revisions: [],
      submissions: [],
      reviews: [],
    },
  };
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
  readonly onSave?: () => Promise<void>;
  readonly onSubmit?: () => Promise<void>;
  readonly onCreateAnnotation?: () => Promise<void>;
  readonly onReview?: (
    decision: RuntimeReviewDecision,
    comment: string,
  ) => Promise<void>;
  readonly onDiscardChanges?: () => void;
  readonly onReportDataIssue?: (input: DataIssueReportInput) => Promise<void>;
  readonly noDraft?: boolean;
  readonly permissions?: Partial<AnnotationWorkbenchPermissions>;
}) {
  const mode = input.mode ?? "annotation";
  const fixture = createVisualAnnotationBundle({
    mode,
    cameraCount: input.cameraCount,
  });
  const bundle = input.noDraft ? bundleWithoutDraft(fixture) : fixture;
  const tags =
    mode === "annotation"
      ? (bundle.draft?.tags ?? [])
      : (bundle.history.revisions.at(-1)?.tags ?? []);
  const onTagsChange = vi.fn<(tags: readonly RuntimeAnnotationTag[]) => void>();
  const onSave =
    input.onSave ?? vi.fn<() => Promise<void>>().mockResolvedValue();
  const onRestoreRevision =
    input.onRestoreRevision ??
    vi.fn<(targetRevision: number) => Promise<void>>().mockResolvedValue();
  const onOpenRevisions = vi.fn<() => void>();
  const onSubmit =
    input.onSubmit ?? vi.fn<() => Promise<void>>().mockResolvedValue();
  const onCreateAnnotation =
    input.onCreateAnnotation ??
    vi.fn<() => Promise<void>>().mockResolvedValue();
  const onReview =
    input.onReview ??
    vi
      .fn<(decision: RuntimeReviewDecision, comment: string) => Promise<void>>()
      .mockResolvedValue();
  const defaultEditable =
    input.editable ?? (mode === "annotation" && !input.noDraft);
  const permissions: AnnotationWorkbenchPermissions = {
    hasAnnotationDraft: mode === "annotation" && !input.noDraft,
    canCreate: Boolean(input.noDraft),
    canEdit: defaultEditable,
    canSave: defaultEditable,
    canSubmit: defaultEditable,
    canReview: mode === "tag-review" && input.editable !== false,
    canRevise: defaultEditable,
    ...(input.editable === false ? { readOnlyReason: "测试：权限已撤销" } : {}),
    ...input.permissions,
  };
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
      permissions={permissions}
      canReportDataIssue={Boolean(input.onReportDataIssue)}
      renderPanel={panel}
      scope={visualAnnotationScope}
      tags={tags}
      onCreateAnnotation={onCreateAnnotation}
      onDiscardChanges={input.onDiscardChanges}
      onReportDataIssue={input.onReportDataIssue}
      onReview={onReview}
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
    onCreateAnnotation,
    onOpenRevisions,
    onReview,
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

describe("P08 page-level workspace navigation", () => {
  it.each(["annotation", "tag-review"] as const)(
    "keeps ordinary wheels available for page scrolling in %s while allowing modified model zoom",
    (mode) => {
      renderWorkbench({ cameraCount: 4, mode });
      const pose = screen.getByLabelText("机器人姿态同步视图");
      const canvas = document.createElement("canvas");
      pose.append(canvas);
      // Emulate the native wheel listener installed by OrbitControls.
      const zoom = vi.fn((event: WheelEvent) => event.preventDefault());
      canvas.addEventListener("wheel", zoom);

      expect(
        canvas.dispatchEvent(
          new WheelEvent("wheel", {
            bubbles: true,
            cancelable: true,
            deltaY: 120,
          }),
        ),
      ).toBe(true);
      expect(zoom).not.toHaveBeenCalled();

      for (const modifier of ["ctrlKey", "metaKey"]) {
        expect(
          canvas.dispatchEvent(
            new WheelEvent("wheel", {
              bubbles: true,
              cancelable: true,
              deltaY: 120,
              [modifier]: true,
            }),
          ),
        ).toBe(false);
      }
      expect(zoom).toHaveBeenCalledTimes(2);
    },
  );

  it("places work modes above the three-column body and keeps camera tools local", () => {
    renderWorkbench({ cameraCount: 4, mode: "tag-review" });

    const workspace = screen.getByLabelText("页面工作模式与操作");
    const media = screen.getByLabelText("相机与同步信号");
    expect(
      within(workspace).getByRole("tablist", { name: "数据标注工作模式" }),
    ).toBeVisible();
    expect(within(media).queryByRole("tablist")).not.toBeInTheDocument();
    expect(within(media).getByLabelText("显示视频")).toBeVisible();
    expect(
      within(workspace).queryByLabelText("显示视频"),
    ).not.toBeInTheDocument();
  });

  it("defaults to view without a draft, locks unavailable modes, and focuses create", async () => {
    const user = userEvent.setup();
    const { onCreateAnnotation } = renderWorkbench({
      cameraCount: 1,
      noDraft: true,
      permissions: {
        annotationUnavailableReason: "请先创建标注草稿。",
        reviewUnavailableReason: "创建并提交标注后才能进入审核。",
        revisionUnavailableReason: "请先创建标注草稿。",
      },
    });

    const view = workspaceTab("查看");
    const annotation = workspaceTab("标注");
    const review = workspaceTab("Tag 审核");
    const create = screen.getByRole("button", { name: "创建标注" });
    expect(view).toHaveAttribute("aria-selected", "true");
    expect(annotation).toHaveAttribute("aria-disabled", "true");
    expect(review).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("查看模式")).toBeVisible();
    expect(create).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "保存修改" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "提交审核" }),
    ).not.toBeInTheDocument();

    annotation.focus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "请先创建标注草稿。",
    );
    await user.click(annotation);
    expect(view).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByText("请先创建标注草稿。").length).toBeGreaterThan(0);
    expect(create).toHaveFocus();
    expect(onCreateAnnotation).not.toHaveBeenCalled();
  });

  it("calls the real create callback once and enters annotation after the draft arrives", async () => {
    const user = userEvent.setup();
    const fullBundle = createVisualAnnotationBundle({
      mode: "annotation",
      cameraCount: 1,
    });
    const emptyBundle = bundleWithoutDraft(fullBundle);
    let finishCreate: (() => void) | undefined;
    const createRequest = vi.fn(
      () =>
        new Promise<void>((resolvePromise) => {
          finishCreate = resolvePromise;
        }),
    );

    function CreateHarness(): JSX.Element {
      const [created, setCreated] = useState(false);
      const bundle = created ? fullBundle : emptyBundle;
      return (
        <AnnotationWorkbenchView
          bundle={bundle}
          dirty={false}
          mode="annotation"
          permissions={{
            hasAnnotationDraft: created,
            canCreate: !created,
            canEdit: created,
            canSave: created,
            canSubmit: created,
            canReview: false,
            canRevise: created,
            annotationUnavailableReason: "请先创建标注草稿。",
          }}
          renderPanel={panel}
          scope={visualAnnotationScope}
          tags={bundle.draft?.tags ?? []}
          onCreateAnnotation={async () => {
            await createRequest();
            setCreated(true);
          }}
          onReview={() => Promise.resolve()}
          onSave={() => Promise.resolve()}
          onSubmit={() => Promise.resolve()}
          onTagsChange={() => undefined}
        />
      );
    }

    render(<CreateHarness />);
    await user.dblClick(screen.getByRole("button", { name: "创建标注" }));
    expect(createRequest).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "创建标注" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "创建标注" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    await act(async () => finishCreate?.());
    await waitFor(() =>
      expect(workspaceTab("标注")).toHaveAttribute("aria-selected", "true"),
    );
    expect(screen.getByRole("heading", { name: "多级 Tag" })).toBeVisible();
  });

  it("keeps view mode hard read-only across controls, drag events, and shortcuts", () => {
    const { onCreateAnnotation, onSave, onSubmit, onTagsChange } =
      renderWorkbench({ cameraCount: 1, noDraft: true });
    const timeline = screen.getByLabelText("共享视频时间轴区域");
    const surface = timeline.querySelector(".viewer-timeline__surface");
    expect(surface).not.toBeNull();

    fireEvent.pointerDown(surface!, { clientX: 20, pointerId: 1 });
    fireEvent.pointerMove(surface!, { clientX: 160, pointerId: 1 });
    fireEvent.pointerUp(surface!, { clientX: 160, pointerId: 1 });
    fireEvent.keyDown(document, { key: "[" });
    fireEvent.keyDown(document, { key: "]" });
    fireEvent.click(screen.getByRole("button", { name: "设为入点" }));
    fireEvent.click(screen.getByRole("button", { name: "设为出点" }));

    expect(onTagsChange).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onCreateAnnotation).not.toHaveBeenCalled();
  });

  it("supports roving arrow-key mode changes and skips locked modes", async () => {
    const user = userEvent.setup();
    renderWorkbench({ cameraCount: 1 });
    const annotation = workspaceTab("标注");
    annotation.focus();
    await user.keyboard("{ArrowLeft}");
    expect(workspaceTab("查看")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    expect(annotation).toHaveAttribute("aria-selected", "true");
    expect(annotation).toHaveFocus();
  });

  it("protects dirty mode changes with continue, discard, and save choices", async () => {
    const user = userEvent.setup();
    const onDiscardChanges = vi.fn();
    let finishSave: (() => void) | undefined;
    const onSave = vi.fn(
      () =>
        new Promise<void>((resolvePromise) => {
          finishSave = resolvePromise;
        }),
    );
    renderWorkbench({
      cameraCount: 1,
      dirty: true,
      onDiscardChanges,
      onSave,
    });

    await user.click(workspaceTab("查看"));
    let dialog = screen
      .getByRole("button", { name: "继续编辑" })
      .closest('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    expect(
      within(dialog).getByRole("button", { name: "继续编辑" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "放弃修改" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "保存后切换" }),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "继续编辑" }));
    expect(workspaceTab("标注")).toHaveAttribute("aria-selected", "true");

    await user.click(workspaceTab("查看"));
    dialog = screen
      .getByRole("button", { name: "继续编辑" })
      .closest('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "放弃修改" }));
    expect(onDiscardChanges).toHaveBeenCalledOnce();
    expect(workspaceTab("查看")).toHaveAttribute("aria-selected", "true");

    await user.click(workspaceTab("标注"));
    await user.click(workspaceTab("数据修订"));
    dialog = screen
      .getByRole("button", { name: "继续编辑" })
      .closest('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    await user.click(
      within(dialog).getByRole("button", { name: "保存后切换" }),
    );
    expect(onSave).toHaveBeenCalledOnce();
    expect(
      within(dialog).getByRole("button", { name: "保存后切换" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "保存后切换" }),
    ).toHaveAttribute("aria-busy", "true");
    expect(workspaceTab("标注")).toHaveAttribute("aria-selected", "true");
    await act(async () => finishSave?.());
    await waitFor(() =>
      expect(workspaceTab("数据修订")).toHaveAttribute("aria-selected", "true"),
    );
  });

  it("uses one review submit action and approves when no issue is marked", async () => {
    const user = userEvent.setup();
    const { onReview } = renderWorkbench({
      cameraCount: 1,
      mode: "tag-review",
    });
    const workspace = screen.getByLabelText("页面工作模式与操作");
    const dock = screen.getByLabelText("共享视频时间轴区域");
    const submit = within(workspace).getByRole("button", {
      name: "提交审核",
    });
    expect(submit).toBeEnabled();
    expect(
      within(workspace).getAllByRole("button", { name: "提交审核" }),
    ).toHaveLength(1);
    expect(
      within(dock).queryByRole("button", { name: "提交审核" }),
    ).not.toBeInTheDocument();

    await user.click(submit);
    const dialog = screen.getByRole("dialog", { name: "提交审核" });
    expect(dialog).toHaveTextContent("未标记问题");
    await user.click(
      within(dialog).getByRole("button", { name: "确认提交审核" }),
    );
    expect(onReview).toHaveBeenCalledWith("APPROVE", "");
  });

  it("returns the task for modification with the selected annotation location", async () => {
    const user = userEvent.setup();
    const { onReview } = renderWorkbench({
      cameraCount: 1,
      mode: "tag-review",
    });
    const expand = screen.getByRole("button", { name: "问题与意见" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("checkbox", { name: /^标记问题：/u }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "标记当前 Tag" })).toBeDisabled();
    await user.click(expand);
    const issue = screen.getByRole("checkbox", { name: /^标记问题：/u });
    await user.click(issue);
    await user.type(
      screen.getByRole("textbox", { name: /^补充审核意见/u }),
      "边界需要重新确认",
    );

    expect(screen.getByText("1 处")).toBeVisible();
    await user.click(expand);
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("1 处问题")).toBeVisible();
    expect(screen.getByText("已填写意见")).toBeVisible();
    expect(screen.getByLabelText(/^补充审核意见/u)).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "取消当前标记" }));
    expect(screen.getByText("0 处问题")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "标记当前 Tag" }));
    await user.click(expand);
    expect(issue).toBeChecked();
    expect(screen.getByLabelText(/^补充审核意见/u)).toHaveValue(
      "边界需要重新确认",
    );
    await user.click(expand);
    await user.click(screen.getByRole("button", { name: "提交审核" }));
    const dialog = screen.getByRole("dialog", { name: "提交审核" });
    expect(dialog).toHaveTextContent("进入“待修改”");
    await user.click(
      within(dialog).getByRole("button", { name: "确认提交审核" }),
    );
    expect(onReview).toHaveBeenCalledWith(
      "NEEDS_REVISION",
      expect.stringMatching(
        /标注问题：1 处[\s\S]*步区间 \[301, 451\)[\s\S]*边界需要重新确认/u,
      ),
    );
  });

  it("shows a reasoned empty review state instead of disabled review actions", () => {
    renderWorkbench({
      cameraCount: 1,
      mode: "tag-review",
      editable: false,
      permissions: {
        hasAnnotationDraft: false,
        canReview: false,
        reviewUnavailableReason: "标注尚未提交。",
      },
    });
    expect(
      screen.getByText(/当前没有待审核提交。标注尚未提交。/u),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "提交审核" }),
    ).not.toBeInTheDocument();
  });
});

describe("P08 viewer-slot composition", () => {
  it.each(["annotation", "tag-review"] as const)(
    "keeps the synchronized layout and read-only Tag behavior when %s falls back to viewing",
    async (mode) => {
      const user = userEvent.setup();
      const { onTagsChange, onSave, onReview } = renderWorkbench({
        cameraCount: 4,
        mode,
        editable: false,
      });
      expect(workspaceTab("查看")).toHaveAttribute("aria-selected", "true");
      const pose = screen.getByLabelText("机器人姿态同步视图");
      expect(pose.closest('[data-workspace-mode="view"]')).toHaveAttribute(
        "data-workspace-layout",
        "synchronized",
      );
      expect(screen.getByRole("article", { name: "关节角变化" })).toBeVisible();
      const cameras = screen.getByLabelText("数据清单相机视图");
      expect(cameras.querySelectorAll(".viewer-panel")).toHaveLength(4);
      expect(cameras.querySelector(".viewer-robot-panel")).toBeNull();
      expect(screen.getAllByRole("slider")).toHaveLength(1);
      expect(screen.queryByLabelText("Tag 名称 *")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "问题与意见" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "保存修改" }),
      ).not.toBeInTheDocument();
      const tag = screen.getAllByRole("button", {
        name: /起点同步播放全部视频和关节数据/u,
      })[0]!;
      await user.click(tag);
      await user.keyboard("{Delete}");
      expect(onTagsChange).not.toHaveBeenCalled();
      expect(onSave).not.toHaveBeenCalled();
      expect(onReview).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "数据信息" }));
      const drawer = screen.getByRole("dialog", { name: /数据信息/u });
      expect(
        within(drawer).getByRole("heading", { name: "数据查看" }),
      ).toBeVisible();
      expect(within(drawer).getByText("当前数据身份")).toBeVisible();
      await user.click(within(drawer).getByRole("button", { name: /close/i }));
      await waitFor(() =>
        expect(
          screen.queryByRole("dialog", { name: /数据信息/u }),
        ).not.toBeInTheDocument(),
      );
    },
  );

  it("reports a selected annotation range into the unified problem center", async () => {
    const user = userEvent.setup();
    const onReportDataIssue = vi
      .fn<(input: DataIssueReportInput) => Promise<void>>()
      .mockResolvedValue();
    renderWorkbench({
      cameraCount: 1,
      onReportDataIssue,
    });
    const timeline = screen.getByRole("slider", { name: /播放位置/u });
    Object.defineProperties(timeline, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      releasePointerCapture: { configurable: true, value: vi.fn() },
      hasPointerCapture: { configurable: true, value: () => true },
    });
    vi.spyOn(timeline, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 76,
      width: 100,
      height: 76,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(timeline, { pointerId: 9, button: 0, clientX: 20 });
    fireEvent.pointerMove(timeline, { pointerId: 9, clientX: 60 });
    fireEvent.pointerUp(timeline, { pointerId: 9, clientX: 60 });

    const reportButton = screen.getByRole("button", {
      name: "报告数据问题",
    });
    expect(reportButton).toBeEnabled();
    await user.click(reportButton);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByText("报告数据问题")).toBeVisible(),
    );
    await user.type(
      within(dialog).getByRole("textbox", { name: "问题说明" }),
      "自动质检漏掉了这一段连续缺帧。",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "登记到问题数据" }),
    );

    await waitFor(() => expect(onReportDataIssue).toHaveBeenCalledTimes(1));
    expect(onReportDataIssue).toHaveBeenCalledWith(
      expect.objectContaining({
        issueType: "OTHER",
        severity: "MEDIUM",
        note: "自动质检漏掉了这一段连续缺帧。",
      }),
    );
    expect(await screen.findByText(/已登记为问题数据/u)).toBeVisible();
  });

  it.each(
    (["annotation", "tag-review"] as const).flatMap((mode) =>
      [0, 1, 4, 8].map((cameraCount) => ({ mode, cameraCount })),
    ),
  )(
    "$mode keeps $cameraCount Manifest cameras on one shared timeline",
    ({ mode, cameraCount }) => {
      const { bundle } = renderWorkbench({ cameraCount, mode });
      expect(
        screen
          .getByLabelText("数据清单相机视图")
          .querySelectorAll(".viewer-panel:not(.viewer-robot-panel)"),
      ).toHaveLength(4);
      const cameraPanels = screen
        .getByLabelText("数据清单相机视图")
        .querySelectorAll(".viewer-panel:not(.viewer-robot-panel)");
      expect(
        Array.from(cameraPanels).map((panel) =>
          panel.getAttribute("aria-label"),
        ),
      ).toEqual([
        ...(bundle.manifest?.cameras ?? [])
          .slice(0, 4)
          .map((camera) => `${camera.camera_id} test panel`),
        ...Array.from(
          { length: Math.max(0, 4 - Math.min(cameraCount, 4)) },
          (_, index) =>
            `摄像头 ${Math.min(cameraCount, 4) + index + 1} test panel`,
        ),
      ]);
      expect(
        screen
          .getByLabelText("数据清单相机视图")
          .querySelector(".viewer-robot-panel"),
      ).not.toBeInTheDocument();
      expect(
        screen
          .getByLabelText("机器人姿态同步视图")
          .querySelector(".viewer-robot-panel"),
      ).toBeInTheDocument();
      expect(screen.getAllByRole("slider")).toHaveLength(1);
      expect(
        screen.getByText(
          `四路视频 · 已接入 ${Math.min(cameraCount, 4)} / 4 路`,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/视频、机器人姿态和信号共用时间轴/u),
      ).toBeInTheDocument();
    },
  );

  it("reuses camera, pose and curve panels for review without an audit checklist", async () => {
    const user = userEvent.setup();
    const { bundle, onTagsChange } = renderWorkbench({
      cameraCount: 8,
      mode: "tag-review",
    });
    const cameraPanels = () =>
      screen
        .getByLabelText("数据清单相机视图")
        .querySelectorAll(".viewer-panel:not(.viewer-robot-panel)");
    const selector = screen.getByLabelText("显示视频");
    expect(cameraPanels()).toHaveLength(4);
    expect(selector.querySelectorAll("option")).toHaveLength(10);
    expect(screen.getAllByRole("slider")).toHaveLength(1);
    expect(
      screen.getByRole("article", { name: "关节角变化" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("机器人姿态同步视图")).toBeInTheDocument();
    expect(screen.queryByText("六项审核清单")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "保存修改" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Tag 名称 *")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "问题与意见" }));
    const issue = screen.getByRole("checkbox", { name: /^标记问题：/u });
    await user.click(issue);
    await user.selectOptions(selector, "__all__");
    expect(cameraPanels()).toHaveLength(8);
    await user.selectOptions(selector, bundle.manifest!.cameras[5]!.camera_id);
    expect(cameraPanels()).toHaveLength(1);
    expect(cameraPanels()[0]).toHaveAttribute(
      "aria-label",
      `${bundle.manifest!.cameras[5]!.camera_id} test panel`,
    );
    await user.selectOptions(selector, "__quad__");
    expect(cameraPanels()).toHaveLength(4);
    expect(issue).toBeChecked();
    expect(onTagsChange).not.toHaveBeenCalled();
    await user.click(screen.getByText("当前 Tag 与版本对比"));
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
          hasAnnotationDraft: true,
          canCreate: false,
          canEdit: true,
          canSave: true,
          canSubmit: true,
          canReview: false,
          canRevise: true,
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
    await user.type(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
      "稳定",
    );
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
    expect(within(drawer).getAllByText("待标注").length).toBeGreaterThan(0);
    expect(within(drawer).getByText("任务 ID")).toBeVisible();
    expect(within(drawer).getByText("数据结构")).toBeVisible();
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
  it("uses the annotation dock for joint curves instead of Tag summary or automatic annotation", async () => {
    const user = userEvent.setup();
    renderWorkbench({
      cameraCount: 1,
      autoAnnotationPanel: <section aria-label="自动标注测试面板" />,
    });

    expect(screen.getByRole("article", { name: "关节角变化" })).toBeVisible();
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("当前 Tag 概要")).not.toBeInTheDocument();
    await user.click(workspaceTab("查看"));
    expect(screen.getByRole("article", { name: "关节角变化" })).toBeVisible();
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
    await user.click(workspaceTab("数据修订"));
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
    await user.click(workspaceTab("标注"));
    expect(screen.getByRole("article", { name: "关节角变化" })).toBeVisible();
    expect(screen.queryByLabelText("自动标注测试面板")).not.toBeInTheDocument();
  });

  it("switches view, annotation, and immutable history inside one workbench", async () => {
    const user = userEvent.setup();
    const { onOpenRevisions } = renderWorkbench({ cameraCount: 1 });

    await user.click(workspaceTab("查看"));
    expect(screen.getByLabelText("机器人姿态同步视图")).toBeVisible();
    expect(screen.getAllByRole("slider")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "数据信息" }));
    // Ant Design uses the same test-only ID for the tab tooltip and drawer.
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("数据信息", { exact: true })).toBeVisible();
    expect(screen.getByRole("heading", { name: "数据查看" })).toBeVisible();
    expect(
      screen.getByText(/媒体、数值流和时间轴继续使用同一工作台/u),
    ).toBeVisible();
    expect(screen.getAllByRole("slider")).toHaveLength(1);

    await user.click(within(drawer).getByRole("button", { name: /close/i }));

    await user.click(workspaceTab("标注"));
    expect(screen.getByRole("heading", { name: "多级 Tag" })).toBeVisible();

    await user.click(workspaceTab("数据修订"));
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

  it("keeps Tag creation to one compact row without manual hierarchy controls", () => {
    renderWorkbench({ cameraCount: 4 });
    expect(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
    ).toHaveAttribute("placeholder", "输入标签，按 Enter 创建");
    expect(screen.getByText("拖选后自动分级")).toBeVisible();
    expect(screen.queryByLabelText(/当前 Tag · 共/u)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("父级")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("开始步 *")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("结束步（开区间）*"),
    ).not.toBeInTheDocument();
  });

  it("starts every synchronized view from the clicked Tag boundary", async () => {
    const user = userEvent.setup();
    renderWorkbench({ cameraCount: 4 });
    const tag = screen.getByRole("button", {
      name: /从“操作阶段 \/ 抓取动作 \/ 抓取成功”起点同步播放全部视频和关节数据/u,
    });

    await user.click(tag);

    await waitFor(() => expect(tag).toHaveAttribute("aria-current", "time"));
    const currentSeconds = Number.parseFloat(
      screen.getByTestId("viewer-clock-text").getAttribute("title") ?? "0",
    );
    expect(currentSeconds).toBeGreaterThanOrEqual(10.03);
    expect(currentSeconds).toBeLessThan(11);
    expect(screen.getByText(/点击 Tag 从起点同步播放/u)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "暂停" }));
  });

  it.each([
    [600, 700],
    [330, 420],
    [250, 500],
  ])(
    "creates an independent Tag at %i–%i after selecting tag1",
    async (startStep, endStep) => {
      const user = userEvent.setup();
      const bundle = createVisualAnnotationBundle({
        mode: "annotation",
        cameraCount: 1,
      });
      const original = {
        ...bundle.draft!.tags[0]!,
        label: "tag1",
        path: [bundle.draft!.tags[0]!.tag_id],
      };
      let latestTags: readonly RuntimeAnnotationTag[] = [original];
      function Harness(): JSX.Element {
        const [tags, setTags] = useState(latestTags);
        return (
          <AnnotationWorkbenchView
            bundle={bundle}
            dirty
            mode="annotation"
            renderPanel={panel}
            scope={visualAnnotationScope}
            tags={tags}
            permissions={{
              hasAnnotationDraft: true,
              canCreate: false,
              canEdit: true,
              canSave: true,
              canSubmit: true,
              canReview: false,
              canRevise: true,
            }}
            onReview={() => Promise.resolve()}
            onSave={() => Promise.resolve()}
            onSubmit={() => Promise.resolve()}
            onTagsChange={(next) => {
              latestTags = next;
              setTags(next);
            }}
          />
        );
      }
      render(<Harness />);
      await user.click(screen.getByRole("button", { name: /从“tag1”起点/ }));
      expect(screen.getByLabelText("Tag 名称 *")).toHaveValue("tag1");
      const slider = screen.getByRole("slider", { name: "共享播放位置" });
      vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
        bottom: 28,
        height: 28,
        left: 0,
        right: 26_787,
        top: 0,
        width: 26_787,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      fireEvent.pointerDown(slider, {
        button: 0,
        clientX: startStep + 0.5,
        pointerId: 7,
      });
      fireEvent.pointerMove(slider, { clientX: endStep + 0.5, pointerId: 7 });
      fireEvent.pointerUp(slider, { clientX: endStep + 0.5, pointerId: 7 });
      expect(latestTags).toEqual([original]);
      expect(screen.getByLabelText("编辑已有 Tag")).toHaveValue("");
      expect(screen.getByLabelText("Tag 名称 *")).toHaveValue("");
      await user.type(screen.getByLabelText("Tag 名称 *"), "tag2");
      await user.click(screen.getByRole("button", { name: "创建 Tag" }));
      expect(latestTags).toHaveLength(2);
      expect(latestTags[0]).toMatchObject({
        annotation_id: original.annotation_id,
        label: "tag1",
        start_step: original.start_step,
        end_step: original.end_step,
      });
      expect(latestTags[1]).toMatchObject({
        label: "tag2",
        start_step: startStep,
        end_step: endStep,
      });
      expect(latestTags[1]!.annotation_id).not.toBe(original.annotation_id);
      expect(latestTags[1]!.tag_id).not.toBe(original.tag_id);
      if (startStep === 250) {
        expect(latestTags[0]!.parent_annotation_id).toBe(
          latestTags[1]!.annotation_id,
        );
        expect(latestTags[1]!.parent_annotation_id).toBeNull();
      } else if (startStep === 330) {
        expect(latestTags[1]!.parent_annotation_id).toBe(
          original.annotation_id,
        );
      } else {
        expect(latestTags.every((tag) => !tag.parent_annotation_id)).toBe(true);
      }
    },
  );

  it("creates hierarchical Tags, edits their names and intervals, and deletes a parent while keeping its children", async () => {
    const user = userEvent.setup();
    const bundle = createVisualAnnotationBundle({
      mode: "annotation",
      cameraCount: 1,
    });
    let latestTags = bundle.draft?.tags ?? [];

    function ManualTagHarness(): JSX.Element {
      const [tags, setTags] =
        useState<readonly RuntimeAnnotationTag[]>(latestTags);
      return (
        <AnnotationWorkbenchView
          bundle={bundle}
          dirty
          mode="annotation"
          permissions={{
            hasAnnotationDraft: true,
            canCreate: false,
            canEdit: true,
            canSave: true,
            canSubmit: true,
            canReview: false,
            canRevise: true,
          }}
          renderPanel={panel}
          scope={visualAnnotationScope}
          tags={tags}
          onReview={() => Promise.resolve()}
          onSave={() => Promise.resolve()}
          onSubmit={() => Promise.resolve()}
          onTagsChange={(nextTags) => {
            const mutableTags = [...nextTags];
            latestTags = mutableTags;
            setTags(mutableTags);
          }}
        />
      );
    }

    render(<ManualTagHarness />);
    const slider = screen.getByRole("slider", { name: "共享播放位置" });
    Object.defineProperty(slider, "getBoundingClientRect", {
      value: () => ({
        bottom: 28,
        height: 28,
        left: 0,
        right: 26_787,
        top: 0,
        width: 26_787,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });
    Object.defineProperty(slider, "setPointerCapture", { value: vi.fn() });
    const selectSteps = (startStep: number, endStep: number) => {
      fireEvent.pointerDown(slider, {
        button: 0,
        clientX: startStep + 0.5,
        pointerId: 7,
      });
      fireEvent.pointerMove(slider, { clientX: endStep + 0.5, pointerId: 7 });
      fireEvent.pointerUp(slider, { clientX: endStep + 0.5, pointerId: 7 });
    };

    selectSteps(330, 420);
    const rangeStatus = screen.getByText("当前选区").closest('[role="status"]');
    expect(rangeStatus).toHaveTextContent("自动 L2 · 父级 抓取成功");
    await user.type(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
      "夹爪闭合",
    );
    await user.click(screen.getByRole("button", { name: "创建 Tag" }));

    const parent = latestTags.find(
      (tag) => tag.annotation_id === "annotation-grasp-01",
    );
    const child = latestTags.find((tag) => tag.label === "夹爪闭合");
    expect(child).toMatchObject({
      parent_annotation_id: parent?.annotation_id,
    });
    expect(child?.path).toEqual([...(parent?.path ?? []), child?.tag_id]);

    selectSteps(600, 700);
    expect(rangeStatus).toHaveTextContent("自动 L1");
    await user.type(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
      "搬运阶段",
    );
    await user.click(screen.getByRole("button", { name: "创建 Tag" }));

    expect(latestTags.find((tag) => tag.label === "搬运阶段")).toMatchObject({
      parent_annotation_id: null,
    });

    const count = latestTags.length;
    await user.selectOptions(
      screen.getByLabelText("编辑已有 Tag"),
      child!.annotation_id,
    );
    const labelInput = screen.getByLabelText("Tag 名称 *", {
      selector: "#manual-tag-label",
    });
    expect(labelInput).toHaveValue("夹爪闭合");
    expect(
      screen.getByRole("button", { name: "应用 Tag 修改" }),
    ).toBeDisabled();
    await user.clear(labelInput);
    expect(
      screen.getByRole("button", { name: "应用 Tag 修改" }),
    ).toBeDisabled();
    await user.type(labelInput, "夹爪抓紧");
    for (const [boundary, step] of [
      ["标注开始", 280],
      ["标注结束", 500],
    ] as const) {
      fireEvent.pointerDown(
        screen.getByRole("button", { name: new RegExp(boundary) }),
        {
          button: 0,
          clientX: step + 0.5,
          pointerId: 7,
        },
      );
      fireEvent.pointerMove(slider, { clientX: step + 0.5, pointerId: 7 });
      fireEvent.pointerUp(slider, { clientX: step + 0.5, pointerId: 7 });
    }
    await user.click(screen.getByRole("button", { name: "应用 Tag 修改" }));
    expect(latestTags).toHaveLength(count);
    expect(
      latestTags.find((tag) => tag.annotation_id === child!.annotation_id),
    ).toEqual({
      ...child,
      label: "夹爪抓紧",
      start_step: 280,
      end_step: 500,
      parent_annotation_id: null,
      path: [child!.tag_id],
    });
    expect(
      screen.getByRole("button", { name: "应用 Tag 修改" }),
    ).toBeDisabled();

    const parentSegment = screen.getByRole("button", {
      name: /从“夹爪抓紧 \/ 抓取成功”起点/u,
    });
    fireEvent.contextMenu(parentSegment, { clientX: 40, clientY: 40 });
    await user.click(screen.getByRole("menuitem", { name: /删除 Tag/u }));
    expect(latestTags).toHaveLength(count - 1);
    expect(
      latestTags.some((tag) => tag.annotation_id === parent!.annotation_id),
    ).toBe(false);
    expect(
      latestTags.find((tag) => tag.annotation_id === child!.annotation_id),
    ).toMatchObject({
      label: "夹爪抓紧",
      parent_annotation_id: null,
      path: [child!.tag_id],
      start_step: 280,
      end_step: 500,
    });
    expect(screen.getByLabelText("Tag 名称 *")).toHaveValue("");
    expect(screen.getByRole("button", { name: "创建 Tag" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "撤销删除" }));
    expect(latestTags).toHaveLength(count);
    expect(
      latestTags.find((tag) => tag.annotation_id === child!.annotation_id)
        ?.parent_annotation_id,
    ).toBeNull();
    await user.click(
      screen.getByRole("button", {
        name: /从“夹爪抓紧 \/ 抓取成功”起点/u,
      }),
    );
    fireEvent.keyDown(screen.getByLabelText("Tag 名称 *"), { key: "Delete" });
    expect(latestTags).toHaveLength(count);
    fireEvent.keyDown(
      screen.getByRole("button", {
        name: /从“夹爪抓紧 \/ 抓取成功”起点/u,
      }),
      { key: "Delete" },
    );
    expect(latestTags).toHaveLength(count - 1);
  });

  it("cancels edits without mutating Tags and ignores Enter during Chinese composition", async () => {
    const user = userEvent.setup();
    const { onTagsChange } = renderWorkbench({ cameraCount: 1 });
    await user.selectOptions(
      screen.getByLabelText("编辑已有 Tag"),
      "annotation-grasp-01",
    );
    const input = screen.getByLabelText("Tag 名称 *", {
      selector: "#manual-tag-label",
    });
    await user.type(input, "修正");
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(onTagsChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "取消编辑 Tag" }));
    expect(screen.getByLabelText("Tag 名称 *")).toHaveValue("");
    expect(onTagsChange).not.toHaveBeenCalled();
  });

  it("becomes read-only when edit capabilities are revoked", () => {
    renderWorkbench({ cameraCount: 1, editable: false });
    expect(screen.getByText("测试：权限已撤销")).toBeInTheDocument();
    expect(workspaceTab("查看")).toHaveAttribute("aria-selected", "true");
    expect(
      screen.queryByRole("button", { name: "保存修改" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "提交审核" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("开始步 *")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设为入点" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "设为出点" })).toBeDisabled();
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
    expect(workspaceTab("标注")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交审核" })).toBeDisabled();
    expect(screen.getByLabelText("编辑已有 Tag")).toBeDisabled();
    expect(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
    ).toBeDisabled();
  });

  it("derives the review result from issue selection without requiring a comment", async () => {
    const user = userEvent.setup();
    renderWorkbench({ cameraCount: 4, mode: "tag-review" });
    const submit = screen.getByRole("button", { name: "提交审核" });
    expect(submit).toBeEnabled();
    expect(screen.getByText(/尚未标记问题/u)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "问题与意见" }));
    await user.click(screen.getByRole("checkbox", { name: /^标记问题：/u }));
    expect(submit).toBeEnabled();
    expect(screen.getByText(/已标记 1 处问题/u)).toBeVisible();
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

    await user.click(workspaceTab("数据修订"));
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

    await user.click(workspaceTab("数据修订"));
    await user.selectOptions(screen.getByLabelText("回退目标修订"), "1");
    await user.click(screen.getByRole("button", { name: "回退为该修订" }));
    await user.click(screen.getByRole("button", { name: "确认回退为 r1" }));

    const revisionHistory = workspaceTab("数据修订");
    expect(revisionHistory).toHaveAttribute("aria-disabled", "true");
    await user.click(revisionHistory);
    expect(onOpenRevisions).not.toHaveBeenCalled();

    finishRestore?.();
    await waitFor(() =>
      expect(revisionHistory).toHaveAttribute("aria-disabled", "false"),
    );
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

    await user.type(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
      "抓取完成",
    );
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
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute("aria-busy", "true");
    expect(onSubmit).not.toHaveBeenCalled();

    finishCheck(passedPreSubmitChecks);
    const dialog = await screen.findByRole("dialog", {
      name: "确认提交审核？",
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
    expect(
      screen.getByLabelText("Tag 名称 *", { selector: "#manual-tag-label" }),
    ).toBeEnabled();
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
      await screen.findByRole("dialog", { name: "确认提交审核？" }),
    ).toHaveTextContent("检查已通过");
    expect(onPreSubmitCheck).toHaveBeenCalledTimes(2);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
