// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

it("submits the chosen combination through the platform and reads back the trainer's status", async () => {
  const scope = {
    organizationId: "org",
    projectId: "proj",
    regionCode: "region",
  };
  const caps = ["training.read", "training.create"] as const;
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "user", displayName: "User", roleIds: [] },
    "session-test",
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
    roleVersion: "test",
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

  const option = (id: string) => ({ id, label: id.toUpperCase() });
  const command = {
    dataset_id: "pick",
    model_id: "act",
    card_id: "gpu",
    config_id: "baseline",
    parameters: { steps: 8 },
  };
  const run = {
    id: "run-17",
    dataset: option("pick"),
    model: option("act"),
    card: option("gpu"),
    config: option("baseline"),
    parameters: { steps: 8 },
    status: "queued",
    created_at: "2026-11-17T00:00:00Z",
  };
  const submissions: string[] = [];
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (input: string, init: RequestInit) => {
    const url = new URL(input);
    expect(url.origin).toBe("http://localhost");
    const prefix = "/api/v1/projects/proj/regions/region/model-training/";
    expect(url.pathname.startsWith(prefix)).toBe(true);
    const resource = url.pathname.slice(prefix.length);
    calls.push(resource);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-test");
    if (resource === "runs" && init.method === "POST") {
      expect(JSON.parse(String(init.body))).toEqual(command);
      submissions.push(headers.get("Idempotency-Key")!);
      if (submissions.length === 1)
        return Response.json({ detail: "timeout" }, { status: 504 });
      return Response.json(run, { status: 202 });
    }
    if (resource === "runs") {
      const items =
        submissions.length >= 2
          ? [
              {
                ...run,
                status: "running",
                progress_percent: 37,
                status_message: "步骤 3",
              },
            ]
          : [];
      return Response.json({ items, total: items.length });
    }
    if (resource === "datasets")
      return Response.json({ items: [option("pick"), option("other")] });
    expect(url.searchParams.get("dataset_id")).toBe("pick");
    if (resource === "models") return Response.json({ items: [option("act")] });
    expect(url.searchParams.get("model_id")).toBe("act");
    if (resource === "cards") return Response.json({ items: [option("gpu")] });
    expect(resource).toBe("configs");
    expect(url.searchParams.get("card_id")).toBe("gpu");
    return Response.json({
      items: [{ ...option("baseline"), parameters: { steps: 4 } }],
    });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <Component />
    </QueryClientProvider>,
  );
  const start = screen.getByRole("button", { name: "开始训练" });
  expect(start).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "选择模型" })).toBeDisabled();
  async function choose(name: string, label: string) {
    await user.click(screen.getByRole("combobox", { name }));
    await user.click(
      await screen.findByText(label, {
        selector: ".ant-select-item-option-content",
      }),
    );
  }
  await choose("选择数据", "PICK");
  await choose("选择模型", "ACT");
  await choose("选择训练卡", "GPU");
  await choose("选择参数配置", "BASELINE");
  const steps = screen.getByRole("spinbutton", { name: "steps" });
  await user.clear(steps);
  await user.type(steps, "8");
  await user.click(start);
  expect(await screen.findByText("训练提交未确认")).toBeVisible();
  await user.click(start);
  expect(await screen.findByText("训练中")).toBeVisible();
  expect(await screen.findByText("步骤 3")).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toMatch(/^[a-f0-9-]{36}$/);
  expect(submissions[1]).toBe(submissions[0]);
  await waitFor(() => expect(start).toBeDisabled());
  expect([...new Set(calls.filter((x) => x !== "runs"))]).toEqual([
    "datasets",
    "models",
    "cards",
    "configs",
  ]);
}, 15000);
