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

it("uses the collection task, reuses its project guide and links the saved Episode draft", async () => {
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
    if (path.endsWith("/project/annotation-tasks"))
      return Response.json([
        task,
        {
          ...task,
          task_id: "other",
          collection_task_id: "collection-b",
          collection_task_code: "031",
          display_label: "任务 31 · Episode 2",
        },
      ]);
    if (path.endsWith("/annotation-tasks/anno")) return Response.json(task);
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
    if (name === "Episode")
      expect(screen.queryByText("任务 31 · Episode 2")).toBeNull();
    await user.click(option);
  }
  await choose("采集任务", "任务 30");
  await choose("Episode", "任务 30 · Episode 1");
  expect(await screen.findByText("抓取")).toBeVisible();
  await choose("项目标注规范", "拿取规范");
  expect(screen.getByText("辨认抓取阶段")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "开始模型预标注" }));
  expect(
    await screen.findByRole("link", { name: "在数据标注中查看" }),
  ).toHaveAttribute("href", "/annotations/tasks/anno");
  expect(requests).toHaveLength(1);
});
