// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JSX } from "react";
import { createDomainError } from "../../shared/api/domain-error";
import type { ViewerPanelRenderContext } from "../../features/viewer";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { RuntimeAnnotationTag } from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

afterEach(() => cleanup());

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
  readonly externalError?: unknown;
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
  render(
    <AnnotationWorkbenchView
      bundle={bundle}
      dirty={false}
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
      onSave={() => Promise.resolve()}
      onSubmit={() => Promise.resolve()}
      onTagsChange={onTagsChange}
    />,
  );
  return { bundle, onTagsChange };
}

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
});

describe("P08 hierarchy, capabilities and conflicts", () => {
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
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
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
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
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
});
