// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { FailedMcapRetry } from "./FailedMcapRetry";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const scope = { organizationId: "org", projectId: "p", regionCode: "global" };

it.each(["full", "qc"] as const)("submits only the selected operation in %s mode", async mode => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({
    matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  }) });
  const files = Array.from({ length: 12 }, (_, i) => ({ raw_source_id: "raw-" + i,
    source_path: i + ".mcap", processing_id: "p-" + i, recording_id: i ? null : "rec",
    stage: i ? "PREPARATION" : "QUALITY" }));
  const post = vi.fn();
  vi.mocked(request).mockImplementation(async options => {
    if (options.method === "POST") { post(options); return {}; }
    return { plan: { revision: 7, scheme: { scheme_id: "test", version: 6 } }, items: files };
  });
  render(<QueryClientProvider client={new QueryClient()}><FailedMcapRetry scope={scope} taskId="task" /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "批量重试失败项" }));
  await screen.findByText("v6");
  if (mode === "qc") fireEvent.click(screen.getByRole("radio", { name: "仅重新质检" }));
  const n = mode === "full" ? 12 : 1;
  fireEvent.click(await screen.findByRole("button", { name: `全选全部失败项（${n}）` }));
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: `重试所选（${n}）` }));
  await screen.findByText(`已提交 ${n} 条${mode === "full" ? "全流程重试" : "重新质检"}`);
  expect(post).toHaveBeenCalledTimes(n);
  for (const [call] of post.mock.calls) {
    if (mode === "full") {
      expect(call.path).toMatch(/:reprocess-mcap$/);
      expect(call.body.plan_revision).toBe(7);
      expect(call.body.failed_selection.collection_task_id).toBe("task");
    } else expect(call.path).toMatch(/\/rec\/quality:retry$/);
  }
});

it("keeps dashboard selection within the displayed issues and pins each task plan", async () => {
  const post = vi.fn();
  vi.mocked(request).mockImplementation(async options => {
    if (options.method === "POST") { post(options); return {}; }
    const t = options.path.includes("/tasks/a/") ? "a" : "b";
    const version = t === "a" ? 6 : 3;
    const item = { raw_source_id: "raw-" + t, source_path: t + ".mcap", processing_id: "file-" + t,
      recording_id: "rec-" + t, stage: t === "a" ? "EPISODE_QUALITY" : "PREPARATION" };
    return { plan: { revision: version + 1, scheme: { scheme_id: t, version } },
      items: [item, { ...item, processing_id: "hidden-" + t, recording_id: "hidden-" + t }] };
  });
  render(<QueryClientProvider client={new QueryClient()}><FailedMcapRetry scope={scope} targets={[
    { taskId: "a", name: "任务甲", issueKeys: new Set(["rec-a"]) },
    { taskId: "b", name: "任务乙", issueKeys: new Set(["file-b"]) },
  ]} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "批量重试失败项" }));
  fireEvent.click(await screen.findByRole("button", { name: "全选全部失败项（2）" }));
  expect(screen.getByText("成片质检")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重试所选（2）" }));
  await screen.findByText("已提交 2 条全流程重试");
  expect(post.mock.calls.map(([o]) => [o.body.failed_selection.collection_task_id,
    o.body.failed_selection.processing_ids, o.body.plan_revision]).sort()).toEqual([
    ["a", ["file-a"], 7], ["b", ["file-b"], 4],
  ]);
});
