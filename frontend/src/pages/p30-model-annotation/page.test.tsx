// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { Component } from "./page";

afterEach(() => {
  cleanup();
  useShellStore.getState().clearSensitiveState();
  resetRuntimeConfigForTests();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("keeps selections across filters, identifies missing labels and retains only failed work for retry", async () => {
  const scope = {
    organizationId: "org",
    projectId: "project",
    regionCode: "region",
  };
  const caps = ["annotation_task.read", "annotation.save"] as const;
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "alice", displayName: "Alice", roleIds: [] },
    "session-model",
  );
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: ["region"],
        projectWide: false,
        capabilities: [...caps],
      },
    ],
    1,
  );
  store.setScope(scope);
  store.setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: "1",
    capabilities: [...caps],
    fetchedAt: new Date().toISOString(),
  });
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const styles = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((el) => styles(el));
  const task = {
    task_id: "anno",
    collection_task_id: "collection-a",
    collection_task_code: "030",
    display_label: "任务 30 · Episode 1",
    project_id: "project",
    region_code: "region",
    dataset_id: "shared",
    data_ref: { identity: { episode_id: "ep-a" } },
    allowed_tags: ["抓取"],
    etag: "rev0",
    current_revision: 0,
    status: "DRAFT",
    assignee_id: "alice",
  };
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (input: string, init: RequestInit) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/model-annotation/episodes")) {
      const episode = {
        ...task,
        label: task.display_label,
        episode_id: "ep-a",
        tag_count: 0,
        uploads: [{ batch_number: 5, uploaded_at: "2020-02-01T15:00:00Z" }],
      };
      const recent = {
        batch_number: 6,
        uploaded_at: new Date(Date.now() - 3600000).toISOString(),
      };
      return Response.json([
        episode,
        {
          ...episode,
          task_id: "second",
          label: "任务 31 · Episode 2",
          episode_id: "ep-b",
          tag_count: 2,
          collection_task_id: "collection-b",
          collection_task_code: "031",
          uploads: [recent],
        },
        {
          ...episode,
          task_id: "blocked",
          label: "任务 30 · Episode 3",
          allowed_tags: [],
          uploads: [recent, ...episode.uploads],
        },
      ]);
    }
    if (path.endsWith("/annotation-tasks/anno")) return Response.json(task);
    if (path.endsWith("/annotation-tasks/second"))
      return Response.json({
        ...task,
        task_id: "second",
        etag: "rev2",
        current_revision: 2,
      });
    if (path.endsWith("/model-annotation/tasks/second/run")) {
      expect(new Headers(init.headers).get("If-Match")).toBe("rev2");
      expect(JSON.parse(String(init.body)).expected_revision).toBe(2);
      requests.push(path);
      return Response.json(
        { title: "草稿已更新", status: 409 },
        { status: 409 },
      );
    }
    if (path.endsWith("/model-annotation/guides"))
      return Response.json([
        {
          name: "拿取规范",
          revision: 4,
          instructions: "从接触到松开",
          prompt: "辨认抓取阶段",
        },
      ]);
    if (path.endsWith("/model-annotation/tasks/anno/run")) {
      expect(new Headers(init.headers).get("If-Match")).toBe("rev0");
      expect(JSON.parse(String(init.body))).toMatchObject({
        guide: "拿取规范",
        guide_revision: 4,
        expected_revision: 0,
      });
      requests.push(path);
      return Response.json({ task_id: "anno", revision: 1, added: 1 });
    }
    throw Error(path);
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Component />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  async function choose(name: string, value: string) {
    await user.click(screen.getByRole("combobox", { name }));
    const option = await screen.findByText(value, {
      selector: ".ant-select-item-option-content",
    });
    await user.click(option);
  }
  const check = (n: number) =>
    screen.queryByRole("checkbox", {
      name: `选择 任务 ${n === 2 ? 31 : 30} · Episode ${n}`,
    });
  expect(await screen.findByText("采集方案尚未配置可用标签")).toBeVisible();
  expect(check(3)).toBeEnabled();
  await choose("采集任务", "任务 30");
  await user.click(screen.getByRole("button", { name: "未标注" }));
  expect(check(2)).toBeNull();
  await user.click(screen.getByRole("button", { name: "选择全部匹配项" }));
  expect(check(1)).toBeChecked();
  expect(check(3)).toBeChecked();
  await user.click(screen.getByRole("button", { name: "移除这些项" }));
  expect(check(3)).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "重置筛选" }));
  await choose("上传批次", "批次 5");
  await user.keyboard("{Escape}");
  await user.click(screen.getByPlaceholderText("开始时间"));
  await user.click(await screen.findByText("最近 7 天"));
  expect(check(1)).toBeNull();
  expect(check(3)).toBeNull();
  expect(await screen.findByText("没有符合筛选条件的 Episode")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "重置筛选" }));
  await user.click(screen.getByRole("button", { name: "已有标注" }));
  expect(check(1)).toBeNull();
  await user.click(check(2)!);
  await choose("项目标注规范", "拿取规范");
  expect(screen.getByText("辨认抓取阶段")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "开始模型预标注" }));
  expect(
    await screen.findByRole("link", { name: "在数据标注中查看" }),
  ).toHaveAttribute("href", "/annotations/tasks/anno");
  expect(await screen.findByText("执行结果 · 成功 1，失败 1")).toBeVisible();
  expect(requests).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "重置筛选" }));
  expect(check(1)).not.toBeChecked();
  expect(check(2)).toBeChecked();
});
