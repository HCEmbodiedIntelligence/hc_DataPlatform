// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { PackageBatchPanel } from "./PackageBatchPanel";
import {
  createPackageBatch,
  listPackageJobs,
  loadPackageCandidates,
} from "../../features/exports/package-deliveries";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";

vi.mock("../../features/exports/package-deliveries", () => ({
  createPackageBatch: vi.fn(),
  listPackageJobs: vi.fn(),
  loadPackageCandidates: vi.fn(),
  commandPackageJob: vi.fn(),
  downloadPackageJob: vi.fn(),
}));
vi.mock("./CollectionAnnotationChooser", () => ({
  CollectionAnnotationChooser: () => <span>固定修订选择</span>,
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});
function mount(annotated = false, canDownload = true) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
  const f = packageDeliveryFixture();
  vi.mocked(loadPackageCandidates).mockResolvedValue(f.candidates);
  vi.mocked(listPackageJobs).mockResolvedValue([]);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PackageBatchPanel
        scope={f.scope}
        datasets={[{ id: f.data.identity.dataset_id, name: "演示数据" }]}
        annotated={annotated}
        canReadAnnotations={true}
        canCreate={true}
        canDownload={canDownload}
      />
    </QueryClientProvider>,
  );
  return f;
}
it("shows RGB depth exclusion and retains the key after an interrupted request", async () => {
  const f = mount();
  vi.mocked(createPackageBatch)
    .mockRejectedValueOnce(new Error("响应中断"))
    .mockResolvedValue(f.job);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(
    screen.getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" }),
  );
  expect(
    screen.getByText("RGB 子集将排除深度视频、深度像素掩码及相关数据特征"),
  ).toBeVisible();
  const create = screen.getByRole("button", { name: "创建标准包交付任务" });
  await user.click(create);
  expect(await screen.findByText("响应中断")).toBeVisible();
  await user.click(create);
  await waitFor(() => expect(createPackageBatch).toHaveBeenCalledTimes(2));
  const calls = vi.mocked(createPackageBatch).mock.calls;
  expect(calls[0]![2]).toBe("rgb");
  expect(calls[0]![3]).toBe(calls[1]![3]);
  expect(calls[0]![1]).toEqual([{ data: f.data, annotation: null }]);
});
it("requires an explicit annotation selection for every selected Episode", async () => {
  const f = mount(true),
    user = userEvent.setup();
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  expect(
    screen.getByRole("button", { name: "创建标准包交付任务" }),
  ).toBeDisabled();
  expect(
    screen.getByText("请为每条 Episode 明确选择标注修订和分段轨道。"),
  ).toBeVisible();
  expect(
    screen.getByRole("radio", { name: "独立标准包批量 ZIP（含已采集深度）" }),
  ).toBeDisabled();
});
it("does not fetch candidates or cached jobs without download permission", () => {
  mount(false, false);
  expect(loadPackageCandidates).not.toHaveBeenCalled();
  expect(listPackageJobs).not.toHaveBeenCalled();
});
