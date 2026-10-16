// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AnnotationNavigator,
  defaultAnnotationNavigatorPreferences,
} from "./AnnotationNavigator";
import { createVisualAnnotationBundle } from "./testing/annotation-fixture";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationTask,
} from "../../features/annotation/runtime/runtime-annotation-adapter";

afterEach(cleanup);

const base = createVisualAnnotationBundle().task;
const tasks: readonly RuntimeAnnotationTask[] = [
  { ...base, task_id: "1", status: "APPROVED" },
  { ...base, task_id: "2", status: "DRAFT", assignee_id: "another-annotator" },
  { ...base, task_id: "3", status: "SUBMITTED" },
  { ...base, task_id: "4", status: "NEEDS_REVISION" },
  { ...base, task_id: "5", status: "DRAFT", dataset_id: "another-dataset" },
];

function setup(
  current = tasks[0]!,
  mode: AnnotationWorkbenchMode = "annotation",
) {
  const onSelect = vi.fn();
  function Harness() {
    const [preferences, onChange] = useState(
      defaultAnnotationNavigatorPreferences,
    );
    return (
      <AnnotationNavigator
        task={current}
        tasks={tasks}
        items={tasks.map((task) => ({
          id: task.task_id,
          label: `Episode ${task.task_id}`,
          description: task.dataset_id,
          status: task.status,
        }))}
        principalId={base.assignee_id!}
        mode={mode}
        preferences={preferences}
        onChange={onChange}
        disabled={false}
        onSelect={onSelect}
      />
    );
  }
  render(<Harness />);
  return { onSelect, user: userEvent.setup() };
}

describe("annotation navigation", () => {
  it("switches sequentially and skips finished, submitted and other people's drafts for next pending", async () => {
    const { user, onSelect } = setup();
    expect(screen.getByRole("button", { name: "上一条" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下一条" }));
    expect(onSelect).toHaveBeenLastCalledWith("2");
    await user.click(screen.getByRole("button", { name: "下一条待标注" }));
    expect(onSelect).toHaveBeenLastCalledWith("4");
    expect(
      screen.queryByRole("button", { name: "切换到标注数据 Episode 5" }),
    ).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "仅当前数据集" }));
    expect(
      screen.getByRole("button", { name: "切换到标注数据 Episode 5" }),
    ).toBeVisible();
  });

  it("preserves filters through collapse and keeps the current item when it does not match", async () => {
    const { user } = setup();
    await user.type(
      screen.getByRole("textbox", { name: "搜索标注数据" }),
      "Episode 4",
    );
    await user.click(screen.getByRole("button", { name: "收起标注数据" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "展开标注数据" }));
    expect(screen.getByRole("textbox")).toHaveValue("Episode 4");
    expect(
      screen.getByRole("button", { name: "切换到标注数据 Episode 1" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "切换到标注数据 Episode 4" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "切换到标注数据 Episode 2" }),
    ).toBeNull();
  });

  it("uses current task state over an old list entry", async () => {
    const { user, onSelect } = setup({ ...tasks[3]!, status: "APPROVED" });
    expect(
      within(
        screen.getByRole("button", { name: "切换到标注数据 Episode 4" }),
      ).getByText("标注完成"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "下一条待标注" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "上一条" }));
    expect(onSelect).toHaveBeenLastCalledWith("3");
  });

  it("finds pending review items when opened in review mode", async () => {
    const { user, onSelect } = setup(tasks[3], "tag-review");
    await user.click(screen.getByRole("button", { name: "下一条待审核" }));
    expect(onSelect).toHaveBeenLastCalledWith("3");
  });
});
